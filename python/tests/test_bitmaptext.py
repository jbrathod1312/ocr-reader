"""
The reader for machine-drawn, fixed-pitch bitmap text.

The point of this reader is that a page drawn by one machine prints every `5`
as the same pixels, so a glyph's identity is its shape and a recogniser only has
to be right about it more often than wrong. These tests build such a page out of
made-up glyphs — the shapes mean nothing, they are only drawn the same for the
same character — and give the recogniser deliberately wrong opinions to show the
vote corrects them. No font and no real recogniser are involved, so the test
runs anywhere.

    .venv/bin/python -m pytest python/tests/test_bitmaptext.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.bitmaptext import read_bitmap_pages  # noqa: E402
from reader.boxes import WordBox  # noqa: E402

#: The page's grid: one character every PITCH pixels, each glyph GLYPH_W by
#: GLYPH_H, resting on a baseline.
PITCH = 30
GLYPH_W = 18
GLYPH_H = 26
LINE_H = 44
MARGIN = 40

#: An alphabet of eight characters, each a distinct connected shape: a full-
#: height left stroke (so every glyph is one piece and the right height) plus a
#: pattern of horizontal bars that is the character's own.
ALPHABET = "0123ABCD"


def _glyph(char: str) -> np.ndarray:
    """
    A deterministic ink pattern for `char`: the same every time, its own shape.

    A full frame the same size for every glyph — so each is one connected piece
    of the page's one height — with a pattern of inner bars that is the
    character's own, so two characters never share a bitmap.
    """
    bits = ALPHABET.index(char)
    glyph = np.zeros((GLYPH_H, GLYPH_W), dtype=bool)
    glyph[0:3, :] = True
    glyph[GLYPH_H - 3 : GLYPH_H, :] = True
    glyph[:, 0:3] = True
    for position in range(3):
        if bits & (1 << position):
            row = 5 + position * 6
            glyph[row : row + 3, 3 : GLYPH_W] = True
    return glyph


def _render(lines: list[str]) -> tuple[np.ndarray, dict[int, int]]:
    """A page of `lines` as ink, and the baseline y of each line by its index."""
    width = MARGIN * 2 + PITCH * max(len(line) for line in lines)
    height = MARGIN * 2 + LINE_H * len(lines)
    page = np.zeros((height, width), dtype=bool)
    baselines: dict[int, int] = {}
    for row, line in enumerate(lines):
        top = MARGIN + row * LINE_H
        baselines[row] = top + GLYPH_H
        for column, char in enumerate(line):
            if char == " ":
                continue
            left = MARGIN + column * PITCH + (PITCH - GLYPH_W) // 2
            page[top : top + GLYPH_H, left : left + GLYPH_W] |= _glyph(char)
    return page, baselines


def _opinions(lines: list[str], baselines: dict[int, int], errors=()) -> list[WordBox]:
    """
    A recogniser's words for the page: the true text, with `errors` applied.

    Each `errors` entry is `(line, column, wrong)`: what the recogniser read at
    that cell instead of the truth. One word per line, placed on its baseline.
    """
    swap = {(line, column): wrong for line, column, wrong in errors}
    words: list[WordBox] = []
    for row, line in enumerate(lines):
        text = "".join(swap.get((row, column), char) for column, char in enumerate(line))
        left = MARGIN + (PITCH - GLYPH_W) // 2
        top = baselines[row] - GLYPH_H
        words.append(WordBox(text=text, x=left, y=top, width=PITCH * len(line), height=GLYPH_H, confidence=0.9))
    return words


# Every character of the alphabet appears several times, so each shape is seen
# enough to be a shape and to win a vote.
LINES = [
    "0123ABCD0123",
    "3210DCBA3210",
    "0A1B2C3D0A1B",
    "D0C1B2A30123",
]


def _lines_of(words: list[WordBox]) -> list[str]:
    """The page's lines, the words of each joined by the spaces between them."""
    rows: dict[int, list[WordBox]] = {}
    for word in words:
        rows.setdefault(round(word.y / 10), []).append(word)
    return [" ".join(w.text for w in sorted(rows[key], key=lambda w: w.x)) for key in sorted(rows)]


# --------------------------------------------------------------------------- #
# The reading                                                                  #
# --------------------------------------------------------------------------- #


def test_a_clean_page_reads_exactly():
    page, baselines = _render(LINES)
    frame = (page.shape[1], page.shape[0])
    out = read_bitmap_pages({1: page}, {1: _opinions(LINES, baselines)}, {1: frame})
    assert _lines_of(out[1]) == LINES


def test_a_minority_misread_is_corrected_by_the_vote():
    # The recogniser reads one `0` as `8` — a character the page never prints.
    # Every other `0` is read right, so the `0` shape is named `0`, and the
    # misread cell comes out `0` too.
    page, baselines = _render(LINES)
    frame = (page.shape[1], page.shape[0])
    out = read_bitmap_pages(
        {1: page}, {1: _opinions(LINES, baselines, errors=[(0, 0, "8")])}, {1: frame}
    )
    assert _lines_of(out[1]) == LINES


def test_a_shape_read_differently_each_time_still_settles_on_the_majority():
    page, baselines = _render(LINES)
    frame = (page.shape[1], page.shape[0])
    # Three different wrong readings of the first column's `0`s, but most of the
    # `0`s across the page are right, so the shape is still `0`.
    errors = [(0, 0, "8"), (1, 4, "9"), (2, 0, "7")]
    out = read_bitmap_pages({1: page}, {1: _opinions(LINES, baselines, errors)}, {1: frame})
    assert _lines_of(out[1]) == LINES


def test_the_spaces_come_from_the_pitch_not_the_recogniser():
    # A line with a gap of one empty cell: the reader puts a space there from
    # the pitch, whatever the recogniser said about spacing.
    lines = ["0123ABCD 0123", "ABCD0123 ABCD", "0A1B2C3D 0A1B", "3D2C1B0A 3D2C"]
    page, baselines = _render(lines)
    frame = (page.shape[1], page.shape[0])
    out = read_bitmap_pages({1: page}, {1: _opinions(lines, baselines)}, {1: frame})
    assert _lines_of(out[1]) == lines


# --------------------------------------------------------------------------- #
# What it declines                                                             #
# --------------------------------------------------------------------------- #


def test_a_page_that_is_not_on_a_grid_is_declined():
    # Glyphs scattered at random x, not on any pitch: not this kind of page.
    rng = np.random.default_rng(0)
    page = np.zeros((600, 800), dtype=bool)
    for _ in range(200):
        x = int(rng.integers(0, 800 - GLYPH_W))
        y = int(rng.integers(0, 600 - GLYPH_H))
        page[y : y + GLYPH_H, x : x + GLYPH_W] |= _glyph(ALPHABET[int(rng.integers(0, 8))])
    out = read_bitmap_pages({1: page}, {1: []}, {1: (800, 600)})
    assert 1 not in out


def test_a_nearly_blank_page_is_declined():
    page, baselines = _render(["01"])
    out = read_bitmap_pages({1: page}, {1: _opinions(["01"], baselines)}, {1: (page.shape[1], page.shape[0])})
    assert 1 not in out
