"""
A page's table, read from where its figures and text sit and nothing else.

Nothing is assumed about the table: not how many columns it has, not what its
rows are called, not that it is an inventory or a list or an invoice. A row is
a line whose figures line up with the figures of other lines; a column is a
stretch of the page where those rows' ink gathers, with clear space either
side; a title is whatever the page prints over a column, if it prints one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from ..boxes import WordBox, median
from ..dates import settled_date


def strip_marks(text: str) -> str:
    return re.sub(r"^[^\w$(]+|[^\w)%]+$", "", text.strip())


def is_figure(text: str) -> bool:
    """
    A token that is a figure rather than a word: it has digits, and at most a
    letter or two of mark beside them (`40.00C`, `200X`). A long bare run of
    digits is an identifier, not a figure that sits in a column.
    """
    token = strip_marks(text)
    digits = sum(char.isdigit() for char in token)
    letters = sum(char.isalpha() for char in token)
    if digits == 0 or letters > 2:
        return False
    return not re.fullmatch(r"\d{5,}", token)


@dataclass(slots=True)
class Block:
    left: float
    right: float


@dataclass(slots=True)
class Table:
    rows: list[list[WordBox]] = field(default_factory=list)  # one line of words per row
    blocks: list[Block] = field(default_factory=list)
    cells: list[list[str]] = field(default_factory=list)
    #: The words of each cell, row by row: what `cells` was made from.
    cell_words: list[list[list[WordBox]]] = field(default_factory=list)
    title: str | None = None


def _merge(spans: Sequence[tuple[float, float]], slack: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + slack:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


#: What figures are written with, whatever column they are in.
NUMBER_MARKS = set(".,$-/%()")


def _alphabet(tokens: Sequence[WordBox]) -> set[str]:
    """The punctuation a column's figures are made of: the usual, and what the column adds."""
    counts: dict[str, int] = {}
    for word in tokens:
        for char in set(strip_marks(word.text)):
            if not char.isdigit() and not char.isalpha():
                counts[char] = counts.get(char, 0) + 1
    return NUMBER_MARKS | {char for char, n in counts.items() if n >= max(2, len(tokens) * 0.15)}


def _fits(word: WordBox, alphabet: set[str], length: int | None) -> bool:
    token = strip_marks(word.text)
    if length is not None and len(token) != length:
        return False
    # Letters are marks (`40.00C`, `200X`); what rules a token out is punctuation
    # the column never uses, like the colons of a time.
    return all(char.isdigit() or char.isalpha() or char in alphabet for char in token)


def _y_groups(words: Sequence[WordBox], height: float) -> list[list[float]]:
    """The vertical positions words sit at: those within a fraction of a line are one."""
    ys: list[list[float]] = []
    for y in sorted(w.centre_y for w in words):
        if ys and y - sum(ys[-1]) / len(ys[-1]) <= height * 0.6:
            ys[-1].append(y)
        else:
            ys.append([y])
    return ys


def rows_by_figures(words: Sequence[WordBox], char: float) -> list[list[WordBox]]:
    """
    The rows of the page, found from its figures.

    Figures are clustered by where they sit. A cluster that many words fall in is
    a column of figures; a figure belongs to it only if it is made of the same
    kinds of characters (a time is not an amount) and, where the column's figures
    are all one width, is that width. Each vertical position that has such a
    figure is a row, and every word is given to the nearest row.
    """
    figures = [w for w in words if is_figure(w.text)]
    clusters = _merge([(w.x, w.right) for w in figures], char * 0.5)
    members: list[list[WordBox]] = [[] for _ in clusters]
    for w in figures:
        for c, (left, right) in enumerate(clusters):
            if min(w.right, right) - max(w.x, left) >= w.width * 0.5:
                members[c].append(w)
    tall = median([w.height for w in figures]) or 10.0
    spans = [len(_y_groups(group, tall)) for group in members]
    top = max(spans, default=0)
    anchors: list[WordBox] = []
    for group, rows_spanned in zip(members, spans):
        if rows_spanned < max(3, math.ceil(top * 0.4)):
            continue
        alphabet = _alphabet(group)
        lengths = [len(strip_marks(w.text)) for w in group]
        common = max(set(lengths), key=lengths.count)
        uniform = common if lengths.count(common) >= len(lengths) * 0.7 else None
        anchors += [w for w in group if _fits(w, alphabet, uniform)]
    if len(anchors) < 3:
        return []
    height = median([w.height for w in anchors]) or 10.0
    centres = [sum(group) / len(group) for group in _y_groups(anchors, height)]
    if len(centres) < 3:
        return []
    pitch = median([b - a for a, b in zip(centres, centres[1:])]) or height
    rows: list[list[WordBox]] = [[] for _ in centres]
    for w in words:
        nearest = min(range(len(centres)), key=lambda i: abs(w.centre_y - centres[i]))
        if abs(w.centre_y - centres[nearest]) <= pitch * 0.6:
            rows[nearest].append(w)
    return [row for row in rows if len(row) >= 2]


def cluster(values: Sequence[tuple[float, int]], tolerance: float) -> list[tuple[float, set[int]]]:
    """Positions where several rows have something: (centre, the rows that do)."""
    ordered = sorted(values)
    groups: list[list[tuple[float, int]]] = []
    for position, row in ordered:
        if groups and position - groups[-1][-1][0] <= tolerance:
            groups[-1].append((position, row))
        else:
            groups.append([(position, row)])
    return [(sum(p for p, _ in g) / len(g), {r for _, r in g}) for g in groups]


@dataclass(slots=True)
class Anchor:
    """A column: where cells begin (`start`) or end (`end`) down most of the rows."""

    side: str
    x: float


def find_anchors(rows: Sequence[list[WordBox]], char: float) -> list[Anchor]:
    """
    The positions the rows line up on.

    A column of text lines up on the left, a column of figures on the right (or
    on both, if its figures are the same width). A position is a column's where
    most rows start or end a word there.
    """
    tolerance = max(4.0, char * 0.45)
    need = max(3, math.ceil(len(rows) * 0.75))
    starts = cluster([(w.x, i) for i, row in enumerate(rows) for w in row], tolerance)
    ends = cluster([(w.right, i) for i, row in enumerate(rows) for w in row], tolerance)
    anchors = [Anchor("start", x) for x, hits in starts if len(hits) >= need]
    anchors += [Anchor("end", x) for x, hits in ends if len(hits) >= need]
    return sorted(anchors, key=lambda a: a.x)


def _matches(position: float, anchors: Sequence[Anchor], side: str, tolerance: float) -> Anchor | None:
    near = [a for a in anchors if a.side == side and abs(a.x - position) <= tolerance]
    return min(near, key=lambda a: abs(a.x - position)) if near else None


def split_row(row: Sequence[WordBox], anchors: Sequence[Anchor], char: float) -> list[tuple[list[WordBox], Anchor | None]]:
    """
    A row cut into cells at the positions the rows line up on.

    A word that starts where a column starts begins a cell; a word that ends
    where a column ends closes one; and a wide gap between words is a cut
    either way. Each cell is keyed by the column it belongs to, if any.
    """
    tolerance = max(6.0, char * 0.9)
    cells: list[list[WordBox]] = []
    for word in sorted(row, key=lambda w: w.x):
        previous = cells[-1][-1] if cells and cells[-1] else None
        begins = (
            previous is None
            or _matches(word.x, anchors, "start", tolerance) is not None
            or word.x - previous.right >= char * 2.2
            or _matches(previous.right, anchors, "end", tolerance) is not None
        )
        if begins:
            cells.append([word])
        else:
            cells[-1].append(word)
    keyed: list[tuple[list[WordBox], Anchor | None]] = []
    for cell in cells:
        key = _matches(cell[0].x, anchors, "start", tolerance) or _matches(cell[-1].right, anchors, "end", tolerance)
        keyed.append((cell, key))
    return keyed


def _split_glued(row: Sequence[WordBox], anchors: Sequence[Anchor], tolerance: float) -> list[WordBox]:
    """
    A run of digits wide enough to span several columns, cut into the columns.

    The recogniser sometimes returns a row of counts as one token. When k column
    starts fall inside it and its digits divide evenly among them, it is k cells.
    """
    out: list[WordBox] = []
    starts = [a.x for a in anchors if a.side == "start"]
    for word in row:
        token = re.sub(r"-", "", strip_marks(word.text)) if re.fullmatch(r"\d+(?:-\d+)+", strip_marks(word.text)) else strip_marks(word.text)
        inside = [x for x in starts if word.x - tolerance <= x <= word.right - tolerance]
        if re.fullmatch(r"\d{5,}", token) and len(inside) >= 2 and len(token) % len(inside) == 0:
            size = len(token) // len(inside)
            for index, x in enumerate(inside):
                out.append(
                    WordBox(
                        text=token[index * size : (index + 1) * size],
                        x=x,
                        y=word.y,
                        width=word.width / len(inside),
                        height=word.height,
                        confidence=word.confidence,
                    )
                )
        else:
            out.append(word)
    return out


_LOOKALIKE = str.maketrans("OoQDIl|", "0000111")
THOUSANDS = re.compile(r"^(\$?\d{1,3})((?:[.,]\d{3})+)([.,]\d{2})?([A-Za-z]?)$")


def repair_token(token: str, strip_tail: bool = True) -> str:
    """
    A misread figure put right, by its shape alone.

    A letter in the middle of digits is a digit; dots between groups of three
    are thousands; a figure's trailing punctuation is noise.
    """
    core = token.strip()
    digits = sum(c.isdigit() for c in core)
    odd = sum(c in "OoQDIl|" for c in core)
    # `1.00O.000`: mostly digits and punctuation, with a letter standing in for one.
    if odd and digits >= 2 and digits + odd + sum(c in ".,$-" for c in core) == len(core.rstrip("C")):
        core = core.translate(_LOOKALIKE)
    core = re.sub(r"[^\w$)%]+$", "", core) if strip_tail and digits >= 2 else core
    match = THOUSANDS.match(core)
    if match:
        head, groups, cents, mark = match.groups()
        core = head + groups.replace(".", ",") + (("." + cents[1:]) if cents else "") + mark
    return core


def repair_columns(cells: list[list[str]]) -> None:
    """Repairs by column: what a column is made of says how a misread token in it was meant."""
    if not cells:
        return
    for c in range(len(cells[0])):
        column = [row[c] for row in cells if row[c]]
        if not column:
            continue
        tokens = [t for cell in column for t in cell.split()]
        dollars = sum(t.startswith("$") for t in tokens) >= 2
        figures = sum(1 for t in tokens if is_figure(t))
        numeric = figures >= len(tokens) * 0.6
        dated = sum(1 for cell in column if settled_date(cell) is not None) >= len(column) * 0.6
        for row in cells:
            if not row[c]:
                continue
            if dated and len(row[c].split()) == 1:
                row[c] = settled_date(row[c]) or row[c]
                continue
            fixed = []
            parts = row[c].split()
            if numeric and any(is_figure(t) for t in parts):
                # A figure's column holds figures: a stray mark beside one is noise.
                parts = [t for t in parts if is_figure(t) or len(strip_marks(t)) > 2]
            for token in parts:
                if is_figure(token):
                    token = repair_token(token, strip_tail=numeric)
                if dollars and re.match(r"^S\d", token):
                    token = "$" + token[1:]
                fixed.append(token)
            row[c] = " ".join(fixed)


def _redistribute_figures(
    cell_words: list[list[list[WordBox]]],
    columns: Sequence[tuple[str, int]],
    tolerance: float,
) -> None:
    """
    Figures put in the column they are nearest, in order.

    A photograph drifts: a row near the foot of the page can sit a few characters
    left of the rows above it, so its figures miss the exact positions the others
    line up on. A figure belongs to the nearest figure column, and a row's
    figures keep their left-to-right order across the columns.
    """
    count = len(columns)
    centres: list[list[float]] = [[] for _ in range(count)]
    for row in cell_words:
        for c, words in enumerate(row):
            centres[c] += [w.centre_x for w in words if is_figure(w.text)]
    figure_columns = [c for c in range(count) if len(centres[c]) >= max(3, len(cell_words) * 0.4)]
    if len(figure_columns) < 2:
        return
    mid = {c: median(centres[c]) for c in figure_columns}
    spacing = median([abs(mid[b] - mid[a]) for a, b in zip(figure_columns, figure_columns[1:])]) or tolerance * 4
    reach = max(spacing * 0.8, tolerance * 2)
    for row in cell_words:
        figures = sorted(
            ((c, w) for c in figure_columns for w in row[c] if is_figure(w.text)),
            key=lambda cw: cw[1].x,
        )
        pool = figures
        if not pool:
            continue
        placed: dict[int, list[WordBox]] = {}
        last = -1
        moved: set[int] = set()
        for _, word in pool:
            nearest = min(figure_columns, key=lambda c: abs(word.centre_x - mid[c]))
            if abs(word.centre_x - mid[nearest]) > reach:
                continue
            if nearest <= last:
                later = [c for c in figure_columns if c > last]
                if not later or abs(word.centre_x - mid[later[0]]) > reach:
                    continue
                nearest = later[0]
            placed.setdefault(nearest, []).append(word)
            moved.add(id(word))
            last = nearest
        if not moved:
            continue
        for c in range(count):
            row[c] = [w for w in row[c] if id(w) not in moved]
        for c, words in placed.items():
            row[c] = sorted(row[c] + words, key=lambda w: w.x)


def read_table(words: Sequence[WordBox]) -> Table | None:
    usable = [w for w in words if w.text.strip() and w.width > 0 and w.height > 0]
    if len(usable) < 6:
        return None
    char = median([w.width / len(w.text.strip()) for w in usable]) or 10.0
    rows = rows_by_figures(usable, char)
    if len(rows) < 3:
        return None
    anchors = find_anchors(rows, char)
    if len(anchors) < 2:
        return None
    tolerance = max(6.0, char * 0.9)
    rows = [_split_glued(row, anchors, tolerance) for row in rows]
    # The table's left and right edges are those of its first and last columns:
    # what lies outside them — a margin's lettering, a mark beside the figures —
    # is not a cell.
    first, last = anchors[0], anchors[-1]
    # The edges are those of the first and last columns — what lies outside them,
    # a margin's lettering or a mark beside the figures, is not a cell — but only
    # where those columns are figures. A column of text may start further left on
    # some rows (an indented section), and a word there is a word.
    def holds_figures(anchor: Anchor) -> bool:
        tokens = [
            w
            for row in rows
            for w in row
            if abs((w.x if anchor.side == "start" else w.right) - anchor.x) <= tolerance
        ]
        return len(tokens) > 0 and sum(is_figure(w.text) for w in tokens) >= len(tokens) * 0.6

    left_edge = first.x - tolerance if holds_figures(first) else -math.inf
    if holds_figures(last):
        ends = [
            w.right
            for row in rows
            for w in row
            if abs((w.x if last.side == "start" else w.right) - last.x) <= tolerance
        ]
        right_edge = (median(ends) if last.side == "start" and ends else last.x) + tolerance
    else:
        right_edge = math.inf
    rows = [[w for w in row if left_edge <= w.x <= right_edge] for row in rows]
    segmented = [split_row(row, anchors, char) for row in rows if row]
    usage: dict[tuple[str, int], int] = {}
    for cells in segmented:
        for _, key in cells:
            if key is not None:
                usage[(key.side, round(key.x))] = usage.get((key.side, round(key.x)), 0) + 1
    columns = sorted(
        {k for k, n in usage.items() if n >= max(3, math.ceil(len(segmented) * 0.4))}, key=lambda k: k[1]
    )
    if len(columns) < 2:
        return None
    kept_rows = [row for row in rows if row]
    table = Table(rows=kept_rows, blocks=[Block(x, x) for _, x in columns])
    index = {k: i for i, k in enumerate(columns)}
    cell_words: list[list[list[WordBox]]] = []
    for cells in segmented:
        out: list[list[WordBox]] = [[] for _ in columns]
        previous_cell: list[WordBox] | None = None
        previous_index = -1
        for position, (cell, key) in enumerate(cells):
            text = " ".join(w.text.strip() for w in cell)
            if key is not None and (key.side, round(key.x)) in index:
                i = index[(key.side, round(key.x))]
            elif previous_cell is not None and cell[0].x - previous_cell[-1].right < char * 2.2:
                # Cut off from its neighbour only because a word ended where a
                # column does: it is the rest of that cell.
                i = previous_index
            elif len(text) > 2:
                x = cell[0].x
                i = min(range(len(columns)), key=lambda c: abs(columns[c][1] - x))
            else:
                continue
            # A short mark far out in the margin, apart from everything else on
            # the row, is not part of it.
            ahead = cells[position + 1][0][0].x - cell[-1].right if position + 1 < len(cells) else 0.0
            if position == 0 and len(strip_marks(text)) <= 4 and not any(is_figure(w.text) for w in cell) and ahead >= char * 4:
                continue
            out[i] = sorted(out[i] + list(cell), key=lambda w: w.x)
            previous_cell, previous_index = cell, i
        cell_words.append(out)
    _redistribute_figures(cell_words, columns, tolerance)
    table.cell_words = cell_words
    table.cells = [[" ".join(w.text.strip() for w in cell) for cell in row] for row in cell_words]
    # Sparse rows at either end — a footer, a stray line — are not the table.
    def full(row: list[str]) -> bool:
        # A row of totals carries several aligned figures; a footer carries one at most.
        figures = sum(1 for cell in row if cell and all(is_figure(t) for t in cell.split()))
        return sum(1 for cell in row if cell) >= len(columns) * 0.75 or figures >= 2
    start, end = 0, len(table.cells)
    while start < end and not full(table.cells[start]):
        start += 1
    while end > start and not full(table.cells[end - 1]):
        end -= 1
    table.cells = table.cells[start:end]
    table.cell_words = table.cell_words[start:end]
    table.rows = table.rows[start:end]
    repair_columns(table.cells)
    return table
