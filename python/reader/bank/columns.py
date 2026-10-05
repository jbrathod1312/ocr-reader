"""
A ledger's columns, learned from where its figures and text sit.

No word is read for its meaning here. A date is a date because of its shape, a
figure because of its shape, and a column is a stretch of the page where the
rows' ink gathers with clear space either side. What a column is *for* — whether
it adds to the balance or takes from it — is worked out from the arithmetic in
`checks`, and what it is called is whatever the page prints above it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Sequence

from ..boxes import WordBox, group_into_lines, line_band, median
from ..rows import restore_date_digits
from .money import is_money

#: A date as a statement prints it: `09/30/2026`, `9-30-26`, `2026-09-30`.
DATE = re.compile(r"^(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})$")
#: Icons set into a page — a sort arrow, a chevron — arrive as private-use characters.
_GLYPHS = re.compile(r"[-]")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", _GLYPHS.sub("", text)).strip()


def is_date_token(text: str) -> bool:
    # A separator read twice (`09/15//2026`) is still the date.
    squeezed = re.sub(r"([/.\-])\1+", r"\1", clean(text))
    return bool(DATE.match(restore_date_digits(squeezed)))


@dataclass(slots=True)
class Column:
    left: float
    right: float
    #: `date`, `money` or `text`: what the rows hold, by shape.
    kind: str
    label: str = ""

    def distance(self, word: WordBox) -> float:
        """How far a word is from the column: 0 where they overlap."""
        return max(0.0, self.left - word.right, word.x - self.right)


@dataclass(slots=True)
class Group:
    """The lines that belong together: a candidate row."""

    lines: list[list[WordBox]]
    top: float
    bottom: float
    page: int = 0
    anchors: list[WordBox] = field(default_factory=list)

    @property
    def words(self) -> list[WordBox]:
        return [w for line in self.lines for w in line]

    @property
    def dated(self) -> bool:
        return bool(self.anchors)

    @property
    def figured(self) -> bool:
        return any(is_money(w.text) for w in self.words)

    @property
    def is_row(self) -> bool:
        return self.dated and self.figured


def usable(words: Sequence[WordBox]) -> list[WordBox]:
    return [w for w in words if w.text.strip() and w.width > 0 and w.height > 0 and clean(w.text)]


def date_interval(words: Sequence[WordBox], height: float) -> tuple[float, float] | None:
    """
    Where the dates of the rows sit: the horizontal cluster holding the most.

    A date inside a description is a date too, but it does not line up with the
    others. Of several columns of dates, the leftmost wins a tie.
    """
    dates = sorted((w for w in words if is_date_token(w.text)), key=lambda w: w.x)
    clusters: list[list[WordBox]] = []
    for word in dates:
        if clusters and word.x - clusters[-1][-1].x <= height:
            clusters[-1].append(word)
        else:
            clusters.append([word])
    if not clusters:
        return None
    best = max(clusters, key=lambda c: (len(c), -c[0].x))
    return min(w.x for w in best) - height * 0.5, max(w.right for w in best) + height * 0.5


def make_groups(words: Sequence[WordBox], height: float, page: int = 0) -> list[Group]:
    """
    The page's lines, gathered into candidate rows.

    Lines belong together when no blank row separates them. Where one run holds
    several dated lines, it is cut between each pair at the widest gap.
    """
    body = usable(words)
    if not body:
        return []
    interval = date_interval(body, height)
    lines = sorted(group_into_lines(body, 0.5), key=lambda line: line_band(line).top)
    bands = [line_band(line) for line in lines]

    def anchors_of(line: Sequence[WordBox]) -> list[WordBox]:
        if interval is None:
            return []
        return [w for w in line if is_date_token(w.text) and interval[0] <= w.x and w.right <= interval[1]]

    runs: list[list[int]] = [[0]]
    reach = height * 0.45
    for index in range(1, len(lines)):
        if bands[index].top - max(bands[i].bottom for i in runs[-1][-2:]) > reach:
            runs.append([index])
        else:
            runs[-1].append(index)

    pieces: list[list[int]] = []
    for run in runs:
        dated = [i for i in run if anchors_of(lines[i])]
        if len(dated) <= 1:
            pieces.append(run)
            continue
        cuts: list[int] = []
        for before, after in zip(dated, dated[1:]):
            between = [i for i in run if before <= i <= after]
            widest = max(
                range(len(between) - 1),
                key=lambda k: bands[between[k + 1]].top - bands[between[k]].bottom,
            )
            cuts.append(between[widest + 1])
        start = run[0]
        for cut in cuts:
            pieces.append([i for i in run if start <= i < cut])
            start = cut
        pieces.append([i for i in run if i >= start])

    return [
        Group(
            lines=[lines[i] for i in piece],
            top=min(bands[i].top for i in piece),
            bottom=max(bands[i].bottom for i in piece),
            page=page,
            anchors=[w for i in piece for w in anchors_of(lines[i])],
        )
        for piece in pieces
    ]


def _merge(spans: Sequence[tuple[float, float]], slack: float) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + slack:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def induce_columns(rows: Sequence[Group], height: float) -> list[Column]:
    """
    The columns the rows are set in.

    Figures are clustered by where they sit, because a column of right-aligned
    figures is one column however wide each figure is. Dates are the cluster the
    rows were anchored on. Everything else is text, and its columns are the
    stretches of ink the rows leave clear space between.
    """
    if not rows:
        return []
    words = [w for g in rows for w in g.words]
    char = median([w.width / len(w.text.strip()) for w in words if w.text.strip()]) or height * 0.5
    anchors = {id(w) for g in rows for w in g.anchors}
    n = len(rows)

    # Figures.
    figures = [w for w in words if is_money(w.text) and id(w) not in anchors]
    money: list[Column] = []
    for left, right in _merge([(w.x, w.right) for w in figures], char * 0.5):
        inside = [g for g in rows if any(is_money(w.text) and left <= w.centre_x <= right for w in g.words)]
        # A column that shows on a sixth of the rows, or on any row of a short statement.
        if len(inside) >= max(2, math.ceil(n * 0.15)) or (n <= 6 and inside):
            money.append(Column(left, right, "money"))

    # Dates.
    date: list[Column] = []
    if anchors:
        marked = [w for g in rows for w in g.anchors]
        date.append(Column(min(w.x for w in marked), max(w.right for w in marked), "date"))

    def claimed(word: WordBox) -> bool:
        if id(word) in anchors:
            return True
        return any(c.left - 1 <= word.centre_x <= c.right + 1 for c in money) and is_money(word.text)

    # Text: ink the rows share, between the clear space they leave.
    text_columns: list[Column] = []
    free = [(g, [w for w in g.words if not claimed(w)]) for g in rows]
    spans_by_row = [_merge([(w.x, w.right) for w in ws], 0.0) for _, ws in free if ws]
    if spans_by_row:
        low = min(s[0] for spans in spans_by_row for s in spans)
        high = max(s[1] for spans in spans_by_row for s in spans)
        width = int(high - low) + 2
        coverage = [0] * width
        for spans in spans_by_row:
            row = [False] * width
            for start, end in spans:
                for x in range(max(0, int(start - low)), min(width, int(end - low) + 1)):
                    row[x] = True
            for x in range(width):
                coverage[x] += row[x]
        allowed = math.floor(len(spans_by_row) * 0.1)
        narrowest = max(2, int(char * 1.8))
        block: list[int] | None = None
        gap = 0
        blocks: list[tuple[int, int]] = []
        for x in range(width):
            if coverage[x] > allowed:
                if block is None:
                    block = [x, x]
                elif gap >= narrowest:
                    blocks.append((block[0], block[1]))
                    block = [x, x]
                else:
                    block[1] = x
                gap = 0
            else:
                gap += 1
        if block is not None:
            blocks.append((block[0], block[1]))
        text_columns = [Column(low + a, low + b, "text") for a, b in blocks]

    columns = sorted([*date, *text_columns, *money], key=lambda c: c.left)
    return columns


def column_of(word: WordBox, columns: Sequence[Column], height: float) -> int:
    """Which column a word of a row belongs to."""
    if not columns:
        return 0
    if is_money(word.text):
        near = [(c.distance(word), i) for i, c in enumerate(columns) if c.kind == "money"]
        if near:
            distance, index = min(near)
            if distance <= height * 2:
                return index
    texts = [i for i, c in enumerate(columns) if c.kind == "text"]
    dates = [i for i, c in enumerate(columns) if c.kind == "date"]
    if dates and is_date_token(word.text):
        date = columns[dates[0]]
        if date.distance(word) <= height * 0.8:
            return dates[0]
    if texts:
        slack = height * 0.8
        chosen = texts[0]
        for index in texts:
            if columns[index].left - slack <= word.x:
                chosen = index
        return chosen
    return min(range(len(columns)), key=lambda i: columns[i].distance(word))


def cells_of(group: Group, columns: Sequence[Column], height: float) -> list[str]:
    cells: list[list[WordBox]] = [[] for _ in columns]
    for line in group.lines:
        for word in sorted(line, key=lambda w: w.x):
            cells[column_of(word, columns, height)].append(word)
    return [" ".join(w.text.strip() for w in column) for column in cells]
