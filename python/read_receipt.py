#!/usr/bin/env python3
"""
Read a receipt with PP-OCR and emit word boxes as JSON.

Why this exists outside the browser app
---------------------------------------
The app reads these receipts with Tesseract. Its remaining errors are all
character-level and all where the watermark has thinned the print: `/` read as
`1` in a settled date, `8` as `6` in an amount, `L/T` as `LIT`. That is the
failure mode a model trained on photographs is least prone to, so PP-OCR is
worth trying — but wiring it into the browser is blocked on OpenCV.js, which
wedges the main thread for minutes on a 10 MB synchronous WASM init and never
recovers. There is no such problem here.

What this deliberately does NOT do
----------------------------------
Assemble rows. It stops at word boxes; `python/reader/` takes them from there —
glyphs joined, columns found, rows built, the receipt's own checks run. Keeping
the recogniser at the boundary means a PDF's own text and a scan's words enter
the same builders, and only the recognising is paid for when a page needs it.

Usage
-----
    .venv/bin/python python/read_receipt.py frontend/public/samples/weekly-invoice.jpg > words.json
    .venv/bin/python python/read_receipt.py --no-suppress IMAGE   # skip the watermark pass
    .venv/bin/python python/read_receipt.py --scales 1 2          # ensemble over upscales
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

import numpy as np

# --------------------------------------------------------------------------- #
# Watermark suppression                                                        #
# --------------------------------------------------------------------------- #
#
# These constants were tuned against these exact receipts and measured against
# ground truth, not derived from anything. The numbers in `## Measured` in
# python/README.md were read with these; change one and those stop describing
# this reader.

PAPER = 128
TINT_CHROMA = 45
BAR_RUN = 0.03
BAR_RUN_MIN = 36
# The exponent is 2 on purpose. Cubing preserves more of a covered stroke and
# reads like an improvement, but scored over the whole invoice it is worse: it
# darkens the watermark's own strokes too, and the reader starts preferring
# them. See the note on `inkLevel` in color-watermark.ts.
INK_EXPONENT = 2


def _ink_level(stroke: np.ndarray) -> np.ndarray:
    """Push mid-grey overlay toward paper, leave a dark stroke dark."""
    out = stroke.astype(np.float32)
    t = np.clip((out - 48.0) / (PAPER - 48.0), 0.0, None)
    ramped = 48.0 + np.power(t, INK_EXPONENT) * (255.0 - 48.0)
    return np.where(out <= 48.0, out, ramped)


def _vertical_run_stats(red: np.ndarray, stroke: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Length and median stroke of the vertical red run each pixel belongs to.

    A tall run of red is a bar or the body of a stamp; a pixel much darker than
    the rest of its run is the letter the colour crosses. Done per column
    because that is what the TypeScript does, and the two must agree.
    """
    height, width = red.shape
    run_len = np.zeros((height, width), dtype=np.int32)
    run_med = np.zeros((height, width), dtype=np.float32)

    for x in range(width):
        column = red[:, x]
        if not column.any():
            continue
        # Run boundaries: where the mask flips.
        padded = np.concatenate(([False], column, [False]))
        edges = np.flatnonzero(padded[1:] != padded[:-1])
        for start, end in zip(edges[0::2], edges[1::2]):
            values = stroke[start:end, x]
            run_len[start:end, x] = end - start
            run_med[start:end, x] = float(np.median(values))

    return run_len, run_med


def suppress_colored_watermark(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Lift the coloured overlay off the page.

    Printed ink is dark in every channel; a tinted overlay is only dark in the
    channels it absorbs, so the others still carry the page underneath it, in
    whatever colour the overlay is. Returns a greyscale image and the fraction
    of pixels painted out.
    """
    height, width = rgb.shape[:2]
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)

    # Any hue: a watermark is as likely to be blue or green as red. Print is
    # dark in every channel, so what a tint leaves of it is the darkest channel
    # — for a red-dominant pixel that is min(g, b), as it always was.
    chroma = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
    red = chroma >= TINT_CHROMA

    stroke = rgb.min(axis=2).astype(np.float32)
    run_len, run_med = _vertical_run_stats(red, stroke)

    bar_run = max(BAR_RUN_MIN, round(height * BAR_RUN))
    is_bar = (run_len >= bar_run) & (stroke >= run_med - 20)

    level = _ink_level(stroke)
    whiten = red & (is_bar | (stroke >= PAPER))
    level = np.where(whiten, 255.0, level)

    # Pixels that were never red keep their own luminance rather than the
    # green/blue minimum, which would darken neutral print for no reason.
    grey = np.round(0.299 * r + 0.587 * g + 0.114 * b).astype(np.float32)
    out = np.where(red, level, grey)

    ratio = float(whiten.sum()) / float(height * width) if height * width else 0.0
    return np.clip(out, 0, 255).astype(np.uint8), ratio


# --------------------------------------------------------------------------- #
# Line -> words                                                                #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Spaces the recogniser dropped                                                #
# --------------------------------------------------------------------------- #

# Anything darker than this in the cleaned page is print.
INK_LEVEL = 140
# A blank run at least this fraction of the cap height is a word space. Measured
# over all three sample receipts: spaces between words and between count columns
# run 0.33-0.71 of the cap height, gaps between letters and inside amounts 0.29
# at most — except where the watermark pass has thinned a stroke, which widens a
# letter gap to 0.31 (`TH E` in `100X THE CASH`). The cut sits between the two.
SPACE_GAP = 0.32


def ink_gaps(
    page: np.ndarray, x0: int, y0: int, x1: int, y1: int
) -> tuple[list[float], list[int], list[float]]:
    """
    Word-sized blank runs inside a line box, and the glyphs between them.

    Returns the page x of the centre of each run, and for each stretch of ink
    between runs how many separate glyphs it holds (blank columns apart). Read
    off the pixels rather than off the recogniser, because the recogniser is
    what lost the spaces: `RED WHITE & BLUE 7S` comes back as one string, but
    the print still has clean white columns where the spaces are.
    """
    height, width = page.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return [], [], []
    ink = page[y0:y1, x0:x1] < INK_LEVEL
    inked = ink.any(axis=0)
    on = np.flatnonzero(inked)
    if len(on) < 2:
        return [], [], []

    # Cap height from the tall strokes, so a watermark speck above the line
    # does not inflate it and a dot or comma does not shrink it.
    top = ink.argmax(axis=0)
    bottom = ink.shape[0] - 1 - ink[::-1].argmax(axis=0)
    cap = float(np.percentile((bottom - top + 1)[inked], 90))
    if cap <= 0:
        return [], [], []

    gaps: list[float] = []
    widths: list[float] = []
    glyphs: list[int] = [0]
    run_start: int | None = int(on[0])
    for column in range(int(on[0]), int(on[-1]) + 1):
        if not inked[column]:
            if run_start is None:
                run_start = column
            continue
        if run_start is not None:
            if column - run_start >= SPACE_GAP * cap:
                gaps.append(x0 + (run_start + column) / 2)
                widths.append((column - run_start) / cap)
                glyphs.append(0)
            glyphs[-1] += 1
            run_start = None
    return gaps, glyphs, widths


#: Blank run, in cap heights, that cuts a number in two. Digits of one number are
#: set with a little room between them, just over what splits two words; the gap
#: between one numeric column and the next is a great deal wider.
DIGIT_GAP = 0.5


def restore_spaces(
    text: str,
    spans: list[tuple[float, float]],
    gaps: list[float],
    glyphs: list[int] | None = None,
    widths: list[float] | None = None,
) -> tuple[str, list[tuple[float, float]]]:
    """
    Put a space into `text` at each blank run the print shows.

    When the glyphs counted between the runs add up to the characters read,
    each word's length is known exactly and the text is cut by those lengths.
    That is the usual case; it fails where two letters touch or the watermark
    pass broke one in two.

    Otherwise `spans` — the recogniser's x-range per character — says which two
    characters a run falls between. Those ranges come from CTC timesteps and are
    too loose to find spaces by themselves, and can be a letter out on a
    proportional font, which is why they are the fallback.
    """
    if len(spans) != len(text) or len(text) < 2 or not gaps:
        return text, spans

    kept = [index for index, char in enumerate(text) if not char.isspace()]
    if widths and glyphs and len(glyphs) == len(gaps) + 1 and all(glyphs) and sum(glyphs) == len(kept):
        # A narrow gap between two digits is inside a number: join the two runs.
        joined_gaps: list[float] = []
        joined = [glyphs[0]]
        cursor = glyphs[0]
        for k, count in enumerate(glyphs[1:]):
            inside_number = (
                text[kept[cursor - 1]].isdigit() and text[kept[cursor]].isdigit() and widths[k] < DIGIT_GAP
            )
            if inside_number:
                joined[-1] += count
            else:
                joined_gaps.append(gaps[k])
                joined.append(count)
            cursor += count
        gaps, glyphs = joined_gaps, joined
        if not gaps:
            return text, spans
    if glyphs and len(glyphs) == len(gaps) + 1 and all(glyphs) and sum(glyphs) == len(kept):
        out_text: list[str] = []
        out_spans: list[tuple[float, float]] = []
        cursor = 0
        for word, count in enumerate(glyphs):
            if word > 0:
                left = spans[kept[cursor - 1]][1]
                right = spans[kept[cursor]][0]
                out_text.append(" ")
                out_spans.append((min(left, right), max(left, right)))
            for index in kept[cursor : cursor + count]:
                out_text.append(text[index])
                out_spans.append(spans[index])
            cursor += count
        return "".join(out_text), out_spans

    centres = [(left + right) / 2 for left, right in spans]
    after: set[int] = set()
    for gap in gaps:
        best, best_distance = None, float("inf")
        for index in range(len(text) - 1):
            if text[index].isspace() or text[index + 1].isspace():
                continue
            if not centres[index] <= gap <= centres[index + 1]:
                continue
            distance = abs(gap - (centres[index] + centres[index + 1]) / 2)
            if distance < best_distance:
                best, best_distance = index, distance
        if best is not None:
            after.add(best)
    if not after:
        return text, spans

    out_text = []
    out_spans = []
    for index, char in enumerate(text):
        out_text.append(char)
        out_spans.append(spans[index])
        if index in after:
            left = spans[index][1]
            right = spans[index + 1][0]
            out_text.append(" ")
            out_spans.append((min(left, right), max(left, right)))
    return "".join(out_text), out_spans


# --------------------------------------------------------------------------- #
# Re-reading text lines                                                        #

def split_line_into_words(
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    score: float,
    spans: list[tuple[float, float]] | None = None,
) -> list[dict[str, Any]]:
    """
    Cut a recognised line into words, spaced across its box.

    PP-OCR detects lines: `SYSTEM FEE 5.00` arrives as one box and one string.
    The row builders need the pieces separately because they tell a label from
    an amount by where it sits across the page. Each token gets the share of the
    box its characters occupy, spaces included — exact for the monospaced print
    on these receipts, and close enough elsewhere, since what the builders
    compare is which side of the page a token is on.

    With `spans` — the recogniser's x-range per character, in the same
    coordinates as `x` — each piece is placed where its own characters were read
    instead of by its share of the string, which matters on a proportional font
    where `W` is three times `I`.

    Only whitespace cuts a line. A token the recogniser ran together is cut
    later, by the reader, where the page's columns say it spans several.
    """
    line = text.strip()
    if not line or w <= 0 or h <= 0:
        return []
    if spans is not None and len(spans) != len(text):
        spans = None
    if spans is not None:
        lead = len(text) - len(text.lstrip())
        spans = spans[lead : lead + len(line)]

    def place(offset: int, length: int) -> tuple[float, float]:
        if spans is None:
            return x + (w * offset) / span, (w * length) / span
        left = spans[offset][0]
        right = spans[offset + length - 1][1]
        return left, max(right - left, 1e-3)

    span = len(line)
    words: list[dict[str, Any]] = []
    index = 0
    while index < span:
        if line[index].isspace():
            index += 1
            continue
        start = index
        while index < span and not line[index].isspace():
            index += 1
        left, width = place(start, index - start)
        words.append(
            {"text": line[start:index], "x": left, "y": y, "width": width, "height": h, "confidence": score}
        )
    return words


# --------------------------------------------------------------------------- #
# Watermark lettering at the end of a line                                     #
# --------------------------------------------------------------------------- #

# Print is dark in every channel — the brightest of them 40 to 90 on a
# photographed receipt — while a tinted overlay is bright in at least one, of
# whatever hue. So a character whose box holds no pixel darker than this in its
# brightest channel, on a tint, has nothing printed in it: it is the overlay's
# lettering, which the recogniser reads as a letter.
OVERLAY_BRIGHT = 150
OVERLAY_TINT = 0.1


def overlay_only(color: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Whether a character's box on the original page holds only red overlay."""
    height, width = color.shape[:2]
    left, right = max(0, int(x0)), min(width, int(np.ceil(x1)) + 1)
    top, bottom = max(0, int(y0)), min(height, int(np.ceil(y1)))
    if right <= left or bottom <= top:
        return False
    crop = color[top:bottom, left:right].reshape(-1, 3).astype(np.int16)
    brightest = crop.max(axis=1)
    tinted = brightest - crop.min(axis=1) >= TINT_CHROMA
    return bool(np.percentile(brightest, 3) >= OVERLAY_BRIGHT and tinted.mean() >= OVERLAY_TINT)


def is_overlay_line(
    text: str,
    spans: list[tuple[float, float]],
    color: np.ndarray,
    y0: float,
    y1: float,
    scale: float,
) -> bool:
    """Whether every character of a line is overlay, with nothing printed in any of them."""
    if len(spans) != len(text):
        return False
    inked = [
        index
        for index, char in enumerate(text)
        if not char.isspace()
    ]
    # A word of it, not a speck or a figure: a stray mark may be noise as easily
    # as overlay, and a figure is the one thing a table cannot afford to lose.
    letters = sum(1 for i in inked if text[i].isalpha())
    return len(inked) >= 3 and letters >= len(inked) * 0.75 and all(
        overlay_only(color, spans[i][0] / scale, y0 / scale, spans[i][1] / scale, y1 / scale)
        for i in inked
    )


def trim_overlay_edges(
    text: str,
    spans: list[tuple[float, float]],
    color: np.ndarray,
    y0: float,
    y1: float,
    scale: float,
) -> tuple[str, list[tuple[float, float]]]:
    """
    `text` without letters of the red overlay at either end.

    A watermark behind a line's first or last word, in any colour and with any
    lettering, is detected as part of the line — `Arkansas` under `RAFFLE Cashes` comes back as `AdkRAFFLE Cashes` —
    and the letters it adds are not on the page. Only the ends are trimmed, and
    never the whole line: a line of lettering with nothing printed in it might
    be print in a pale ink, and a letter inside a line has print on both sides.
    """
    if len(spans) != len(text) or len(text) < 2:
        return text, spans

    def bare(index: int) -> bool:
        char = text[index]
        left, right = spans[index]
        return not char.isspace() and overlay_only(color, left / scale, y0 / scale, right / scale, y1 / scale)

    start, end = 0, len(text)
    while start < end and (text[start].isspace() or bare(start)):
        start += 1
    while end > start and (text[end - 1].isspace() or bare(end - 1)):
        end -= 1
    # Nothing left that has print in it: leave the line as it was.
    if start >= end or not any(not c.isspace() for c in text[start:end]):
        return text, spans
    # Only trim where letters were actually found to be overlay.
    if not any(bare(i) for i in [*range(0, start), *range(end, len(text))]):
        return text, spans
    return text[start:end], spans[start:end]


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #


def load_engine():
    """
    RapidOCR runs the PP-OCR ONNX models without pulling in PaddlePaddle.

    Same PP-OCR weights, minus a ~100 MB framework that a read-one-image script
    has no use for.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as error:  # pragma: no cover - environment problem
        raise SystemExit(
            "rapidocr-onnxruntime is not installed.\n"
            "    uv pip install -r python/requirements.txt --excludes python/excludes.txt\n"
            f"({error})"
        ) from error
    builds = opencv_builds()
    if len(builds) > 1:
        print(
            f"warning: {len(builds)} OpenCV builds are installed ({', '.join(builds)}).\n"
            "They share one cv2 folder, so which loads depends on install order.\n"
            "Keep only the headless one:\n"
            f"    uv pip uninstall {' '.join(builds)}\n"
            "    uv pip install -r python/requirements.txt --excludes python/excludes.txt",
            file=sys.stderr,
        )
    return RapidOCR()


def opencv_builds() -> list[str]:
    """The OpenCV wheels installed. Every one of them unpacks into `cv2`."""
    from importlib import metadata

    found = []
    for name in (
        "opencv-python",
        "opencv-python-headless",
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
    ):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        found.append(name)
    return found


def read_words(
    engine,
    page: np.ndarray,
    scale: float,
    restore: bool = True,
    color: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    import cv2

    if scale != 1:
        page = cv2.resize(
            page, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
        )

    # The detector wants three channels.
    rgb = cv2.cvtColor(page, cv2.COLOR_GRAY2BGR) if page.ndim == 2 else page
    grey = page if page.ndim == 2 else cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    result, _ = engine(rgb, return_word_box=True)
    if not result:
        return []

    lines: list[tuple[float, float, float, float, str, float, list | None]] = []
    overlay_lines: set[int] = set()
    for position, entry in enumerate(result):
        box, text, score = entry[0], str(entry[1]), float(entry[2])
        char_boxes = entry[3] if len(entry) > 3 else None
        points = np.asarray(box, dtype=np.float32)
        x0, y0 = points[:, 0].min(), points[:, 1].min()
        x1, y1 = points[:, 0].max(), points[:, 1].max()
        # A line set down the margin — `MyArkansasLottery.com` up the edge of a
        # receipt — arrives as a tall, narrow box. Its letters are spread across
        # a sliver and its height reaches over every table line beside it, so
        # it would be read into one of them. A line of text is wider than it is
        # tall; three or more characters in a box twice as tall as wide are not
        # text on a row. The PDF's own reader drops these for the same reason.
        if len(text.strip()) >= 3 and (y1 - y0) > 2 * (x1 - x0):
            continue
        spans = None
        if restore and char_boxes and len(char_boxes) == len(text):
            spans = []
            for char_box in char_boxes:
                xs = np.asarray(char_box, dtype=np.float32)[:, 0]
                spans.append((float(xs.min()), float(xs.max())))
            if color is not None:
                if is_overlay_line(text, spans, color, float(y0), float(y1), scale):
                    overlay_lines.add(len(lines))
                trimmed, kept = trim_overlay_edges(text, spans, color, float(y0), float(y1), scale)
                if trimmed != text:
                    text, spans = trimmed, kept
                    x0 = min(left for left, _ in spans)
                    x1 = max(right for _, right in spans)
            gaps, glyphs, widths = ink_gaps(grey, int(x0), int(y0), int(np.ceil(x1)), int(np.ceil(y1)))
            text, spans = restore_spaces(text, spans, gaps, glyphs, widths)
            spans = [(left / scale, right / scale) for left, right in spans]
        lines.append((x0, y0, x1, y1, text, score, spans))

    # A line that is all overlay — the watermark's own lettering, read as words
    # — has no business in the rows. Dropped only where most of the page is
    # printed in dark ink, so a document set in a pale ink is not emptied.
    if overlay_lines and len(overlay_lines) * 2 < len(lines):
        lines = [line for index, line in enumerate(lines) if index not in overlay_lines]

    words: list[dict[str, Any]] = []
    for x0, y0, x1, y1, text, score, spans in lines:
        words.extend(
            split_line_into_words(
                float(x0) / scale,
                float(y0) / scale,
                float(x1 - x0) / scale,
                float(y1 - y0) / scale,
                text,
                score,
                spans,
            )
        )
    return words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="path to the receipt image")
    parser.add_argument(
        "--no-suppress",
        action="store_true",
        help="skip the watermark pass, to see what it is worth",
    )
    parser.add_argument(
        "--scales",
        nargs="+",
        type=float,
        default=[2.0],
        help="upscales to read at. 2 is measured best on these receipts; passing\n"
        "several emits several passes, which the scorer merges by row",
    )
    parser.add_argument("--max-side", type=int, default=2400, help="downscale longest side to this")
    args = parser.parse_args()

    import cv2

    bgr = cv2.imread(args.image, cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"could not read {args.image}")

    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest > args.max_side:
        factor = args.max_side / longest
        bgr = cv2.resize(bgr, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    if args.no_suppress:
        page = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        ratio = 0.0
    else:
        page, ratio = suppress_colored_watermark(rgb)

    engine = load_engine()
    passes = [
        {"scale": scale, "words": read_words(engine, page, scale, color=rgb)}
        for scale in args.scales
    ]

    json.dump(
        {
            "image": args.image,
            "size": {"width": page.shape[1], "height": page.shape[0]},
            "watermarkPixelRatio": round(ratio, 4),
            "passes": passes,
        },
        sys.stdout,
        indent=1,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
