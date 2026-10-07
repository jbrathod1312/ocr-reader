"""
Text a machine drew from one typeface, read from the glyphs themselves.

A statement that a bank's system printed and then saved as a picture is not a
photograph of paper. Every `5` on it is the same pixels as every other `5`, to
the last one, because the same font put each of them there. So the page can say
what its glyphs are without a recogniser being right about any one of them:
glyphs of one shape are one mark, and which mark it is can be settled by a vote
among every place a recogniser read it. A recogniser that is right nine times in
ten wins that vote by a wide margin, and then the tenth reading, and every
reading it never made, comes out right as well.

What comes out is exact where it comes out at all. This declines whatever is not
that kind of page — a photograph, a proportional face, a scan with noise in it —
and says so by returning nothing for it, so the caller reads it the way it read
every other page. Nothing is looked up and no word is read for its meaning: the
only things assumed are that the page has one character pitch, that its lines
sit on baselines, and that a glyph printed twice looks the same twice.

The steps, each of which is a few lines below:

* the page's ink is cut into connected pieces, and the pieces into printed
  lines by where their bottoms rest;
* a line's pieces are gathered into cells — the dot of an `i` with its stem, two
  letters that touched with each other — and a cell's shape is its identity;
* each shape is named by the recogniser's own text, where that text lines up
  with the cells one for one;
* a blob that two lines' glyphs fused into, such as a comma whose tail touches
  the digit under it, is taken apart with the shapes the page has already
  named: every one of them has to be wholly inside the blob, at its place on its
  line, which is a test the right glyph passes and a wrong one cannot;
* the cells are written out, with spaces where the pitch says there is a gap.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

import cv2
import numpy as np

from .boxes import WordBox

#: A piece of ink smaller than this is a speck, not part of a glyph.
SPECK = 4

#: More pieces than this is a picture, not a page of characters.
MOST_PIECES = 14000
LEAST_PIECES = 40

#: How far from the page's tallest glyph a glyph's height may be and still be
#: one of its ordinary capitals and digits, which is what lines and the pitch
#: are found from. In pixels, as the glyphs are drawn at whatever resolution.
REGULAR_HEIGHT = 3

#: The share of the pitch's phase the glyphs have to agree on. Every glyph is
#: centred in its cell, so on a page set at one pitch this is nearly 1; a page
#: whose blocks are set at other offsets is lower, and still read, because the
#: spaces are counted from one glyph to the next and not from the page's edge;
#: proportional text is near 0.
LEAST_GRID = 0.55

#: Printed lines a page must have for the rest to be believed.
LEAST_LINES = 3

#: A glyph shape has to repeat for the page to be one a machine drew. The share
#: of cells whose shape occurs at least `REPEATED` times must be at least this.
REPEATED = 3
LEAST_REPEATING = 0.8

#: A cell wider than this many pitches holds more than one character.
ONE_CELL = 1.2

#: Pieces whose horizontal spans overlap this much of the narrower are one mark
#: — the dot of an `i` over its stem, the two dots of a colon.
SAME_MARK = 0.5

#: A mark seen fewer times than this, or carrying more ink than the usual glyph
#: of its name, may be two lines' glyphs fused, and is tried against the shapes
#: the page has named. The shapes tried must themselves not be taller than
#: `TALLEST_GLYPH` capitals: a bracket and a `\` are, a fused pair is not a glyph.
TRUSTED_VOTES = 3
EXTRA_INK = 1.1
TALLEST_GLYPH = 1.5

#: A rule — the frame round a table, an underline — is no character. It is
#: either far taller than any glyph, or a thin bar taller than a capital, or far
#: longer than a word. A pair of fused glyphs is none of these: it is glyph-wide.
RULE_TALL = 3.0
RULE_BAR = (0.25, 1.6)
RULE_LONG = 10.0

#: How much of what a blob is made of may be left unexplained after the shapes
#: the page knows are taken out of it, and still count as taken apart.
LEFTOVER = 0.06

#: Weight of a vote that comes from lining a recogniser's text up with the cells
#: where the counts did not agree, against one where they agreed exactly.
ALIGNED_VOTE = 0.5

#: Letters whose capital and small form are one shape at two sizes. The size,
#: which the ink knows exactly, says which the page printed.
SAME_SHAPE = frozenset("COSUVWXZKPM")


@dataclass(slots=True)
class _Piece:
    """One connected piece of ink."""

    id: int
    x: int
    y: int
    w: int
    h: int

    @property
    def bottom(self) -> int:
        return self.y + self.h

    @property
    def centre_x(self) -> float:
        return self.x + self.w / 2

    @property
    def centre_y(self) -> float:
        return self.y + self.h / 2


@dataclass(slots=True)
class _Cell:
    """One printed mark: the ink of a glyph, and how many characters it stands for."""

    x0: int
    x1: int
    y0: int
    y1: int
    #: The pieces it is made of, so its exact pixels can be had again.
    ids: list[int]
    #: Its shape, as the page tells it from every other: size, bits, and height
    #: above the baseline in coarse steps.
    key: tuple
    #: Characters in it: 1, or more where printed glyphs touched.
    count: int
    #: Where it sits against its baseline, exactly.
    dy: int
    #: How many pixels of ink it has.
    ink: int = 0
    #: Set where a cell was found by taking a blob apart, or named from one
    #: sighting of the recogniser's alone, and so named for this mark only.
    named: str | None = None
    #: Whether that name is the recogniser's word and not the page's own shape.
    guessed: bool = False

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(slots=True)
class _Sheet:
    """A page's ink, cut into lines of cells."""

    ink: np.ndarray
    labels: np.ndarray
    cap: float
    pitch: float
    baselines: list[int]
    lines: list[list[_Cell]]


@dataclass(slots=True)
class _Name:
    """What a shape is called, and how sure the page is of it."""

    text: str
    share: float
    votes: float


@dataclass(slots=True)
class _Template:
    """A named shape, kept to find that glyph again inside a blob."""

    text: str
    bits: np.ndarray
    dy: int
    ink: int
    key: tuple
    votes: float = 0.0


# --------------------------------------------------------------------------- #
# The page as cells                                                            #
# --------------------------------------------------------------------------- #


def _pieces(ink: np.ndarray) -> tuple[np.ndarray, list[_Piece]]:
    count, labels, stats, _centres = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    pieces = [
        _Piece(i, int(x), int(y), int(w), int(h))
        for i, (x, y, w, h, area) in enumerate(stats)
        if i and area >= SPECK
    ]
    return labels, pieces


def _cap_height(pieces: Sequence[_Piece]) -> float | None:
    """The height of the page's capitals: its tall marks, and not the fused ones."""
    heights = sorted(p.h for p in pieces if p.h >= 12)
    if len(heights) < 20:
        return None
    return float(heights[int(len(heights) * 0.85)])


def _is_regular(piece: _Piece, cap: float) -> bool:
    return abs(piece.h - cap) <= REGULAR_HEIGHT and 0.35 * cap <= piece.w <= 1.2 * cap


def _pitch(pieces: Sequence[_Piece], cap: float) -> tuple[float, float] | None:
    """
    The distance from one character to the next, and how well the page keeps it.

    Glyphs are centred in their cells, so the centres of ordinary glyphs fall
    at one phase of the pitch. The pitch is the period at which that phase
    gathers most; its strength is the length of the mean unit vector, which is
    1 for a page set on one grid and near 0 for one that is not.
    """
    centres = np.array([p.centre_x for p in pieces if _is_regular(p, cap)])
    if len(centres) < 30:
        return None

    def strength(periods: np.ndarray) -> np.ndarray:
        phase = np.exp(2j * np.pi * centres[None, :] / periods[:, None])
        return np.abs(phase.mean(axis=1))

    coarse = np.arange(0.7 * cap, 1.5 * cap, 0.05)
    found = strength(coarse)
    best = float(found.max())
    # The smallest period that is nearly as good: a multiple of the pitch does
    # as well on a page that leaves every other cell empty.
    at = float(coarse[int(np.argmax(found >= 0.97 * best))])
    fine = np.arange(at - 0.06, at + 0.06, 0.002)
    refined = strength(fine)
    return float(fine[int(np.argmax(refined))]), float(refined.max())


def _baselines(pieces: Sequence[_Piece], cap: float) -> list[int]:
    """Where each printed line's characters rest, top to bottom."""
    bottoms = sorted(p.bottom for p in pieces if _is_regular(p, cap))
    runs: list[list[int]] = []
    for bottom in bottoms:
        if runs and bottom - runs[-1][-1] <= 3:
            runs[-1].append(bottom)
        else:
            runs.append([bottom])
    lines = [(int(np.median(run)), len(run)) for run in runs]
    # A letter that hangs below the line leaves a few bottoms of its own a
    # little under it. That is the same line, and the one with more is the line.
    kept: list[tuple[int, int]] = []
    for at, support in lines:
        if kept and at - kept[-1][0] < 0.45 * cap:
            if support > kept[-1][1]:
                kept[-1] = (at, support)
            continue
        kept.append((at, support))
    return [at for at, _ in kept]


def _is_rule(piece: _Piece, cap: float) -> bool:
    thin, tall = RULE_BAR
    return (
        piece.h > RULE_TALL * cap
        or (piece.w <= thin * cap and piece.h > tall * cap)
        or piece.w > RULE_LONG * cap
    )


def _assign(pieces: Sequence[_Piece], baselines: Sequence[int], cap: float) -> list[list[_Piece]]:
    """Each piece to the line whose middle it is nearest, if it is on one."""
    lines: list[list[_Piece]] = [[] for _ in baselines]
    middles = np.array([b - cap / 2 for b in baselines], dtype=float)
    for piece in pieces:
        if _is_rule(piece, cap):
            continue
        index = int(np.argmin(np.abs(middles - piece.centre_y)))
        base = baselines[index]
        if base - cap - 4 < piece.centre_y < base + 0.4 * cap:
            lines[index].append(piece)
    return lines


def _shape(labels: np.ndarray, ids: Sequence[int], box: tuple[int, int, int, int]) -> tuple[str, int]:
    """A mark's identity — a digest of its exact pixels — and how much ink it has."""
    x0, y0, x1, y1 = box
    mask = np.isin(labels[y0:y1, x0:x1], list(ids))
    return hashlib.md5(np.packbits(mask).tobytes()).hexdigest()[:16], int(mask.sum())


def _cells(
    labels: np.ndarray,
    pieces: Sequence[_Piece],
    base: int,
    pitch: float,
) -> list[_Cell]:
    """A line's pieces gathered into marks, left to right."""
    groups: list[dict] = []
    for piece in sorted(pieces, key=lambda p: p.x):
        if groups:
            last = groups[-1]
            overlap = min(last["x1"], piece.x + piece.w) - max(last["x0"], piece.x)
            if overlap > SAME_MARK * min(last["x1"] - last["x0"], piece.w):
                last["ids"].append(piece.id)
                last["x0"] = min(last["x0"], piece.x)
                last["x1"] = max(last["x1"], piece.x + piece.w)
                last["y0"] = min(last["y0"], piece.y)
                last["y1"] = max(last["y1"], piece.bottom)
                continue
        groups.append(
            {"ids": [piece.id], "x0": piece.x, "x1": piece.x + piece.w, "y0": piece.y, "y1": piece.bottom}
        )
    cells: list[_Cell] = []
    for g in groups:
        width = g["x1"] - g["x0"]
        count = 1 if width <= ONE_CELL * pitch else max(2, round(width / pitch))
        dy = g["y0"] - base
        box = (g["x0"], g["y0"], g["x1"], g["y1"])
        digest, ink = _shape(labels, g["ids"], box)
        key = (width, g["y1"] - g["y0"], digest, round(dy / 8))
        cells.append(_Cell(g["x0"], g["x1"], g["y0"], g["y1"], g["ids"], key, count, dy, ink))
    return cells


def _sheet(ink: np.ndarray) -> _Sheet | None:
    """The page as lines of cells, or None where it is not set on a grid."""
    labels, pieces = _pieces(ink)
    if not LEAST_PIECES <= len(pieces) <= MOST_PIECES:
        return None
    cap = _cap_height(pieces)
    if cap is None:
        return None
    grid = _pitch(pieces, cap)
    if grid is None or grid[1] < LEAST_GRID:
        return None
    pitch = grid[0]
    baselines = _baselines(pieces, cap)
    if len(baselines) < LEAST_LINES:
        return None
    lines = [_cells(labels, line, base, pitch) for line, base in zip(_assign(pieces, baselines, cap), baselines)]
    cells = [c for line in lines for c in line]
    if not cells:
        return None
    # One mark in one place says nothing; a machine's page says everything twice.
    seen = Counter(c.key for c in cells)
    repeating = sum(1 for c in cells if seen[c.key] >= REPEATED) / len(cells)
    if repeating < LEAST_REPEATING:
        return None
    return _Sheet(ink, labels, cap, pitch, baselines, lines)


# --------------------------------------------------------------------------- #
# Naming the shapes                                                            #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Said:
    """One word the recogniser read, placed on a line of the page's cells."""

    line: int
    left: float
    right: float
    text: str


def _printable(text: str) -> bool:
    return bool(text) and all(32 < ord(c) < 127 for c in text)


def _said(sheet: _Sheet, words: Sequence[WordBox], scale: tuple[float, float]) -> list[_Said]:
    """The recogniser's words, in the page's own pixels and on the line each is on."""
    middles = np.array([b - sheet.cap / 2 for b in sheet.baselines], dtype=float)
    reach = 0.5 * float(np.median(np.diff(sheet.baselines))) if len(sheet.baselines) > 1 else sheet.cap
    out: list[_Said] = []
    for word in words:
        text = "".join(word.text.split())
        if word.confidence < 0.5 or not _printable(text):
            continue
        middle = (word.y + word.height / 2) / scale[1]
        index = int(np.argmin(np.abs(middles - middle)))
        if abs(middles[index] - middle) <= reach:
            out.append(_Said(index, word.x / scale[0], word.right / scale[0], text))
    return out


def _under(sheet: _Sheet, said: _Said) -> list[_Cell]:
    """The cells a word stands over: those whose middle is inside its span."""
    slack = 0.15 * sheet.pitch
    return [c for c in sheet.lines[said.line] if said.left - slack <= c.centre <= said.right + slack]


def _expected(cells: Sequence[_Cell]) -> int:
    return sum(c.count for c in cells)


def _align(known: Sequence[str | None], text: str) -> list[int | None]:
    """
    Which of the recogniser's characters each of the cells' characters is,
    allowing either to have dropped or added some.

    `known` is what the cells are so far — a character, or None where the page
    has not named it yet — and an unnamed one may be any character, at a small
    price, so that it is matched rather than left out while a character is
    free to match it. Edit distance, with cells and characters the two strings.
    """
    n, m = len(known), len(text)
    wild = 0.3
    cost = np.zeros((n + 1, m + 1))
    cost[:, 0] = np.arange(n + 1)
    cost[0, :] = np.arange(m + 1)

    def pay(i: int, j: int) -> float:
        if known[i] is None:
            return wild
        return 0.0 if known[i] == text[j] else 1.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i, j] = min(cost[i - 1, j - 1] + pay(i - 1, j - 1), cost[i - 1, j] + 1, cost[i, j - 1] + 1)
    out: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        step = pay(i - 1, j - 1)
        if abs(cost[i, j] - (cost[i - 1, j - 1] + step)) < 1e-9:
            if step < 1.0:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif abs(cost[i, j] - (cost[i - 1, j] + 1)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return out


def _lined_up(cells: Sequence[_Cell], current: Mapping[tuple, _Name], text: str) -> dict[int, str] | None:
    """
    The characters of `text` that belong to the unnamed cells among `cells`, by
    the cell's place in the list; or None where the two do not line up closely.

    Closely means nearly every character found a cell and every cell a
    character: a word the recogniser cut in two or ran into the next is not a
    witness to what any one glyph is.
    """
    expanded: list[str | None] = []
    owner: list[int] = []
    for at, cell in enumerate(cells):
        name = current.get(cell.key) if cell.named is None else None
        chars = [cell.named] if cell.named is not None else None
        if chars is None:
            chars = list(name.text) if name and len(name.text) == cell.count else [None] * cell.count
        expanded += chars
        owner += [at] * cell.count
    if not any(c is None for c in expanded):
        return None
    aligned = _align(expanded, text)
    lost = sum(a is None for a in aligned) + (len(text) - sum(a is not None for a in aligned))
    if lost > max(1, 0.15 * max(len(expanded), len(text))):
        return None
    found: dict[int, str] = {}
    for spot, matched in enumerate(aligned):
        at = owner[spot]
        cell = cells[at]
        if matched is not None and cell.count == 1 and expanded[spot] is None:
            found[at] = text[matched]
    return found


def _vote(sheets: Mapping[int, _Sheet], said: Mapping[int, Sequence[_Said]]) -> dict[tuple, _Name]:
    """Name every shape by what the recogniser said where it lined up with the cells."""
    votes: dict[tuple, Counter] = defaultdict(Counter)

    def names() -> dict[tuple, _Name]:
        out: dict[tuple, _Name] = {}
        for key, counter in votes.items():
            text, weight = counter.most_common(1)[0]
            out[key] = _Name(text, weight / sum(counter.values()), sum(counter.values()))
        return out

    # Where a word and the cells under it agree on how many characters there
    # are, they are taken to be the same characters in the same order.
    for number, sheet in sheets.items():
        for word in said[number]:
            cells = _under(sheet, word)
            if cells and _expected(cells) == len(word.text):
                at = 0
                for cell in cells:
                    votes[cell.key][word.text[at : at + cell.count]] += 1.0
                    at += cell.count

    # Then the words where they did not, lined up as closely as they will go,
    # naming only what is still unnamed — and again, as each round names more.
    for _ in range(3):
        current = names()
        before = len(current)
        for number, sheet in sheets.items():
            for word in said[number]:
                cells = _under(sheet, word)
                if not cells or all(c.key in current for c in cells):
                    continue
                found = _lined_up(cells, current, word.text)
                for at, char in (found or {}).items():
                    if cells[at].key not in current:
                        votes[cells[at].key][char] += ALIGNED_VOTE
        if len(names()) == before:
            break
    return names()


def _fill(
    sheets: Mapping[int, _Sheet],
    said: Mapping[int, Sequence[_Said]],
    names: Mapping[tuple, _Name],
) -> None:
    """
    Name, one by one, the marks no shape names, from what the recogniser said
    where it said it. The last resort, and so the least trusted.
    """
    for number, sheet in sheets.items():
        for word in said[number]:
            cells = _under(sheet, word)
            if not cells or all(c.key in names or c.named is not None for c in cells):
                continue
            found = _lined_up(cells, names, word.text)
            for at, char in (found or {}).items():
                if cells[at].named is None and cells[at].key not in names:
                    cells[at].named = char
                    cells[at].guessed = True


def _sized(name: _Name, key: tuple, cap: float) -> _Name:
    """
    A capital or its small form, told by the size of the ink.

    A recogniser calls a small `c` a `C` more than it should; the page's ink
    knows, because the small one is shorter. Only the letters whose two forms
    are one shape at two sizes are in doubt.
    """
    if len(name.text) != 1:
        return name
    height = key[1]
    letter = name.text
    if letter.isupper() and letter in SAME_SHAPE and height <= 0.85 * cap:
        return _Name(letter.lower(), name.share, name.votes)
    if letter.islower() and letter.upper() in SAME_SHAPE and height >= 0.92 * cap:
        return _Name(letter.upper(), name.share, name.votes)
    return name


# --------------------------------------------------------------------------- #
# Fused glyphs                                                                 #
# --------------------------------------------------------------------------- #


def _templates(sheet: _Sheet, names: Mapping[tuple, _Name]) -> list[_Template]:
    """The page's single-character shapes it is surest of, to be found again in a blob."""
    found: dict[tuple, _Template] = {}
    for line, base in zip(sheet.lines, sheet.baselines):
        for cell in line:
            name = names.get(cell.key)
            if name is None or cell.count != 1 or len(name.text) != 1 or cell.key in found:
                continue
            if name.votes < 2 or cell.height > TALLEST_GLYPH * sheet.cap:
                continue
            mask = np.isin(sheet.labels[cell.y0 : cell.y1, cell.x0 : cell.x1], cell.ids)
            found[cell.key] = _Template(name.text, mask, cell.y0 - base, int(mask.sum()), cell.key, name.votes)
    return sorted(found.values(), key=lambda t: -t.ink)


def _taken_apart(
    sheet: _Sheet,
    cell: _Cell,
    templates: Sequence[_Template],
) -> list[tuple[int, _Cell]] | None:
    """
    The marks a fused blob is made of, as (line index, cell), or None.

    Each known shape is looked for on each line the blob reaches, at the height
    that shape keeps above its baseline, and kept only where all of its ink is
    inside the blob. Larger shapes first, and a shape has to explain ink the
    ones before it did not. What is left must be next to nothing.
    """
    blob = np.isin(sheet.labels[cell.y0 : cell.y1, cell.x0 : cell.x1], cell.ids)
    total = int(blob.sum())
    if total == 0:
        return None
    reachable = [
        index
        for index, base in enumerate(sheet.baselines)
        if cell.y0 < base + 0.4 * sheet.cap and cell.y1 > base - sheet.cap - 4
    ]
    covered = np.zeros_like(blob)
    found: list[tuple[int, _Cell]] = []
    blob_f = blob.astype(np.float32)
    for template in templates:
        height, width = template.bits.shape
        if height > blob.shape[0] or width > blob.shape[1]:
            continue
        bits_f = template.bits.astype(np.float32)
        for index in reachable:
            base = sheet.baselines[index]
            for slack in (0, -1, 1):
                top = base + template.dy + slack - cell.y0
                if top < 0 or top + height > blob.shape[0]:
                    continue
                overlap = cv2.matchTemplate(blob_f[top : top + height], bits_f, cv2.TM_CCORR)[0]
                for shift in np.flatnonzero(overlap >= template.ink - 0.5):
                    placed = np.zeros_like(blob)
                    placed[top : top + height, shift : shift + width] = template.bits
                    if int((placed & ~covered).sum()) < 0.6 * template.ink:
                        continue
                    covered |= placed
                    x0 = cell.x0 + int(shift)
                    part = _Cell(
                        x0, x0 + width, cell.y0 + top, cell.y0 + top + height, [], template.key, 1,
                        template.dy + slack, named=template.text,
                    )
                    found.append((index, part))
                    break
    left = int((blob & ~covered).sum())
    if len(found) < 2 or left > max(6, LEFTOVER * total):
        return None
    return found


def _lines_reached(sheet: _Sheet, cell: _Cell) -> list[int]:
    """The lines a blob has a capital's worth of ink on."""
    blob = np.isin(sheet.labels[cell.y0 : cell.y1, cell.x0 : cell.x1], cell.ids)
    reached = []
    for index, base in enumerate(sheet.baselines):
        top, bottom = int(base - sheet.cap + 2 - cell.y0), int(base - 2 - cell.y0)
        top, bottom = max(top, 0), min(bottom, blob.shape[0])
        if bottom > top and blob[top:bottom].any(axis=1).sum() >= 0.25 * sheet.cap:
            reached.append(index)
    return reached


def _usual(sheets: Mapping[int, _Sheet], names: Mapping[tuple, _Name]) -> dict[str, int]:
    """How much ink the page's most-seen glyph of each single character has."""
    best: dict[str, tuple[float, int]] = {}
    for sheet in sheets.values():
        for line in sheet.lines:
            for cell in line:
                name = names.get(cell.key)
                if name is None or cell.count != 1 or len(name.text) != 1:
                    continue
                if cell.height > TALLEST_GLYPH * sheet.cap:
                    continue
                if name.text not in best or name.votes > best[name.text][0]:
                    best[name.text] = (name.votes, cell.ink)
    return {text: ink for text, (_, ink) in best.items()}


def _suspect(cell: _Cell, name: _Name | None, usual: Mapping[str, int]) -> bool:
    """
    Whether a mark may be more than one glyph.

    One nobody named, or one seen too few times to be sure of, may be two
    lines' glyphs fused. So may one that has plainly more ink than the usual
    glyph of its name: a `6` with the tail of a `\\` on it, named `6` because
    that is what the recogniser said of the line it sits on.
    """
    if cell.count != 1 or cell.named is not None:
        return False
    if name is None or name.votes < TRUSTED_VOTES:
        return True
    return len(name.text) == 1 and cell.ink > EXTRA_INK * usual.get(name.text, cell.ink)


#: Marks tried against the page's shapes, at most, so that a page of unreadable
#: noise that got this far cannot take long to give up on.
MOST_TRIES = 400


def _separate(sheets: Mapping[int, _Sheet], names: Mapping[tuple, _Name]) -> None:
    """
    Take apart, in place, every mark that is two lines' glyphs fused, and try
    the marks whose shape was never named.

    A fused blob that the page's shapes cannot take apart is not dropped from
    the line it does not sit in: that line gets an unnamed mark where the blob
    has ink on it, so it shows as unread rather than as a gap.
    """
    usual = _usual(sheets, names)
    for sheet in sheets.values():
        templates = _templates(sheet, names)
        tries = 0
        for index, line in enumerate(sheet.lines):
            kept: list[_Cell] = []
            moved: list[tuple[int, _Cell]] = []
            for cell in line:
                name = names.get(cell.key)
                if not _suspect(cell, name, usual) or not templates or tries >= MOST_TRIES:
                    kept.append(cell)
                    continue
                tries += 1
                parts = _taken_apart(sheet, cell, templates)
                if parts is not None:
                    moved += parts
                    continue
                kept.append(cell)
                # What a blob has ink on beyond its own line is a mark of that
                # line that it will otherwise go without.
                for other in _lines_reached(sheet, cell):
                    if other != index:
                        moved.append(
                            (
                                other,
                                _Cell(cell.x0, cell.x1, cell.y0, cell.y1, [], ("blob",) + cell.key, 1, cell.dy, cell.ink),
                            )
                        )
            sheet.lines[index] = kept
            for target, part in moved:
                sheet.lines[target].append(part)
    for sheet in sheets.values():
        for line in sheet.lines:
            line.sort(key=lambda c: c.x0)


# --------------------------------------------------------------------------- #
# Writing the page out                                                         #
# --------------------------------------------------------------------------- #

#: What a mark the page never named is written as, and how little it is trusted.
#: A mark named once, or named only by the recogniser's word for that one place,
#: is read below the app's review line (0.55) on purpose: one sighting is the
#: case a fused glyph and a misread glyph both come from, and looks like neither.
UNKNOWN = "?"
UNKNOWN_CONFIDENCE = 0.2
SINGLE_VOTE_CONFIDENCE = 0.5


@dataclass(slots=True)
class _Mark:
    text: str
    x0: int
    x1: int
    confidence: float


def _confidence(name: _Name | None, cell: _Cell) -> float:
    """How far a mark's reading is to be trusted: by how it came to be named."""
    if cell.named is not None:
        # Taken out of a blob by shapes the page had named, or the recogniser's
        # word for this one mark and nothing more.
        return SINGLE_VOTE_CONFIDENCE if cell.guessed else 0.95
    if name is None:
        return UNKNOWN_CONFIDENCE
    if name.votes < 2:
        return SINGLE_VOTE_CONFIDENCE
    return 1.0 if name.share >= 0.8 else 0.8


def _write(
    sheet: _Sheet,
    names: Mapping[tuple, _Name],
    scale: tuple[float, float],
) -> list[WordBox]:
    """The page's words, with the spaces the pitch leaves between them."""
    words: list[WordBox] = []
    for line, base in zip(sheet.lines, sheet.baselines):
        run: list[_Mark] = []
        previous: _Cell | None = None
        for cell in line:
            name = names.get(cell.key)
            if cell.named is not None:
                text = cell.named
            elif name is not None and len(name.text) == cell.count:
                text = name.text
            else:
                text, name = UNKNOWN * cell.count, None
            if previous is not None and run:
                # Distance from the last character of one mark to the first of
                # the next, in pitches: one is adjacent, two is a space.
                last = previous.centre + (previous.count - 1) * sheet.pitch / 2
                first = cell.centre - (cell.count - 1) * sheet.pitch / 2
                if round((first - last) / sheet.pitch) > 1:
                    words.append(_word(run, base, sheet, scale))
                    run = []
            run.append(_Mark(text, cell.x0, cell.x1, _confidence(name, cell)))
            previous = cell
        if run:
            words.append(_word(run, base, sheet, scale))
    return words


def _word(run: Sequence[_Mark], base: int, sheet: _Sheet, scale: tuple[float, float]) -> WordBox:
    left = min(m.x0 for m in run)
    right = max(m.x1 for m in run)
    top = base - sheet.cap
    return WordBox(
        text="".join(m.text for m in run),
        x=left * scale[0],
        y=top * scale[1],
        width=(right - left) * scale[0],
        height=(sheet.cap * 1.3) * scale[1],
        confidence=min(m.confidence for m in run),
    )


# --------------------------------------------------------------------------- #
# The door                                                                     #
# --------------------------------------------------------------------------- #


def grid_pages(pictures: Mapping[int, np.ndarray]) -> set[int]:
    """
    Which of these pictures are a machine's text set on a grid.

    The same test `read_bitmap_pages` applies, but without a recogniser: it is
    read from the ink alone, so a caller can learn which pages are worth paying
    a recogniser for before it pays. A page that is not this kind is left out,
    and read however the caller reads any other page.
    """
    return {number for number, ink in pictures.items() if _sheet(ink) is not None}


def read_bitmap_pages(
    pictures: Mapping[int, np.ndarray],
    opinions: Mapping[int, Sequence[WordBox]],
    frames: Mapping[int, tuple[int, int]],
) -> dict[int, list[WordBox]]:
    """
    The words of every page that is a machine's text set on one grid.

    `pictures` is each page's ink — true where it is dark — at the resolution it
    was drawn at, `opinions` what a recogniser read from the same page, and
    `frames` the width and height of the page the words are to be placed in,
    which is the one the opinions are in. Pages that are not that kind of page
    are left out, and so is a document none of whose pages are.
    """
    sheets: dict[int, _Sheet] = {}
    for number, ink in pictures.items():
        sheet = _sheet(ink)
        if sheet is not None:
            sheets[number] = sheet
    if not sheets:
        return {}

    scales = {
        number: (frames[number][0] / sheet.ink.shape[1], frames[number][1] / sheet.ink.shape[0])
        for number, sheet in sheets.items()
    }
    said = {number: _said(sheet, opinions.get(number, ()), scales[number]) for number, sheet in sheets.items()}
    cap = float(np.median([sheet.cap for sheet in sheets.values()]))
    names = {key: _sized(name, key, cap) for key, name in _vote(sheets, said).items()}
    if not names:
        return {}
    _separate(sheets, names)
    _fill(sheets, said, names)
    return {number: _write(sheet, names, scales[number]) for number, sheet in sheets.items()}
