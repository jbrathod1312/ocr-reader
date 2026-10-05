"""
A lottery page, read as the table it is and checked against its own figures.

Nothing is assumed about the page: not that it is an inventory, a list of
settlements or an invoice, not how many columns it has, not what they are
called. The rows, the columns and any titles over them are found from where the
page's figures and text sit (`table`), and the checks are arithmetic: a row that
restates the columns' sums, a figure printed twice, a total that adds up, a
count stated under the rows.

The user chooses "lottery" to say a document is one of these pages. The words
are copied as printed, never looked up.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..assemble import OcrResult
from ..boxes import WordBox, group_into_lines, line_band, median
from ..columns import ColumnGuide, TableRow
from ..validate import find_totals_row, validate_count, validate_pairs, validate_totals
from .table import Table, is_figure, read_table, strip_marks

_URL = re.compile(r"[A-Za-z]\.[A-Za-z]{2,}|@")


def _has_figures(token: str) -> bool:
    """A token with digits in it: a date, a code, an amount — not a word."""
    return len(re.sub(r"\D", "", token)) >= 2


def _lines(words: Sequence[WordBox]) -> list[list[WordBox]]:
    usable = [w for w in words if w.text.strip() and w.width > 0 and w.height > 0]
    return sorted(group_into_lines(usable, 0.5), key=lambda line: line_band(line).top)


def _lines_above(words: Sequence[WordBox], before: float) -> list[list[WordBox]]:
    """The lines that start above `before`, nearest first."""
    lines = [line for line in _lines(words) if line_band(line).top < before - 1]
    return sorted(lines, key=lambda line: -line_band(line).bottom)


def column_extents(table: Table) -> list[tuple[float, float]]:
    """Where each column sits across the page: the span its words fill, in most rows."""
    spans: list[tuple[float, float]] = []
    for c in range(len(table.blocks)):
        lefts = sorted(min(w.x for w in row[c]) for row in table.cell_words if row[c])
        rights = sorted(max(w.right for w in row[c]) for row in table.cell_words if row[c])
        if lefts:
            spans.append((lefts[len(lefts) // 10], rights[-1 - len(rights) // 10]))
        else:
            spans.append((table.blocks[c].left, table.blocks[c].left))
    return spans


def printed_titles(words: Sequence[WordBox], table: Table, first_top: float) -> tuple[list[str], float | None]:
    """
    What the page prints over each column, if it prints anything: the nearest
    line above the first row that holds no figures and sits over most of the
    columns. Each title word goes to the column it overlaps. A page with no such
    line has no titles, and none are made up.
    """
    extents = column_extents(table)
    # Titles sit directly over the columns: no further above the first row than
    # a row is from the next, or it is a heading of the page, not of a column.
    centres = [median([w.centre_y for w in row if is_figure(w.text)] or [w.centre_y for w in row]) for row in table.rows]
    pitch = median([b - a for a, b in zip(centres, centres[1:])]) or 12
    reach = pitch * 1.8
    for line in _lines_above(words, first_top):
        if first_top - line_band(line).bottom > reach:
            break
        if any(_has_figures(strip_marks(w.text)) for w in line):
            continue
        owner = {}
        for word in line:
            overlap = [
                (min(word.right, right) - max(word.x, left), c) for c, (left, right) in enumerate(extents)
            ]
            best, c = max(overlap)
            owner[id(word)] = c if best > 0 else min(
                range(len(extents)), key=lambda i: abs(word.centre_x - (extents[i][0] + extents[i][1]) / 2)
            )
        covered = set(owner.values())
        if len(line) < 2 or len(covered) < max(2, len(extents) * 0.6):
            continue
        labels = [
            " ".join(w.text.strip() for w in sorted(line, key=lambda w: w.x) if owner[id(w)] == c)
            for c in range(len(extents))
        ]
        return labels, line_band(line).top
    return [""] * len(extents), None


def title_above(words: Sequence[WordBox], before: float) -> str | None:
    """
    The page's own title: the nearest line above the table that is made of words.

    A date, a store number or a code has figures in it; an address or a web
    address has a dot between letters. What is left, nearest the table, is the
    heading the page prints over it. It is copied as read, if the recogniser
    was sure of it.
    """
    for line in _lines_above(words, before)[:8]:
        tokens = [strip_marks(w.text) for w in sorted(line, key=lambda w: w.x)]
        tokens = [t for t in tokens if t]
        text = " ".join(tokens)
        if (
            sum(c.isalpha() for c in text) < 4
            or any(_has_figures(t) for t in tokens)
            or any(_URL.search(w.text) for w in line)
            or min(w.confidence for w in line) < 0.85
        ):
            continue
        return text
    return None


def footer_count(words: Sequence[WordBox], last_bottom: float) -> int | None:
    """The first line under the rows that is words ending in a bare number."""
    for line in _lines(words):
        if line_band(line).top <= last_bottom:
            continue
        ordered = sorted(line, key=lambda w: w.x)
        tail = strip_marks(ordered[-1].text)
        if re.fullmatch(r"\d{1,4}", tail) and any(re.search(r"[A-Za-z]{3,}", w.text) for w in ordered[:-1]):
            return int(tail)
        return None
    return None


def _empty(words: Sequence[WordBox], note: str) -> OcrResult:
    return OcrResult(
        kind="table",
        title=None,
        headers=[],
        table_rows=[],
        tables=[],
        column_bounds=None,
        body_left=None,
        validation=[],
        skipped=[],
        warnings=[note] if note else [],
        word_count=len(words),
    )


def assemble_lottery(
    words: Sequence[WordBox],
    row_overlap_ratio: float = 0.5,
    guide: ColumnGuide | None = None,
) -> OcrResult:
    """
    One page of words as the table it holds.

    `row_overlap_ratio` and `guide` are accepted so this stands where
    `assemble_receipt` does; the rows are found from the figures, and nothing is
    carried from one page to the next.
    """
    if not words:
        return _empty(words, "No words were read. The page may be blank after watermark removal.")
    table = read_table(words)
    if table is None or not table.cells:
        return _empty(words, "No table was found on this page.")

    # Where the first row starts, by its figures: a stray mark above it is not the row.
    first_top = min(w.y for w in table.rows[0] if is_figure(w.text)) if any(is_figure(w.text) for w in table.rows[0]) else min(w.y for w in table.rows[0])
    labels, titles_top = printed_titles(words, table, first_top)
    last_bottom = max(w.bottom for w in table.rows[-1])

    pairs = []
    for cells in table.cells:
        text = next((c for c in cells if c and not is_figure(c.split()[0])), "")
        figures = [c for c in cells if c and is_figure(c.split()[-1])]
        pairs.append((text, figures[-1].split()[-1] if figures else ""))
    validation = validate_totals(table.cells, labels)
    if not validation:
        validation = validate_pairs(pairs) if len(table.blocks) <= 3 else []
    validation += validate_count(len(table.cells), footer_count(words, last_bottom))

    totals = find_totals_row([list(cells) for cells in table.cells])
    table_rows = [
        TableRow(
            cells=list(cells),
            confidence=sum(w.confidence for w in row) / len(row),
            total=totals is not None and index == totals[0],
        )
        for index, (cells, row) in enumerate(zip(table.cells, table.rows))
    ]
    bounds = [block.left for block in table.blocks]
    return OcrResult(
        kind="table",
        title=title_above(words, titles_top if titles_top is not None else first_top),
        headers=labels,
        table_rows=table_rows,
        tables=[
            {
                "headers": labels,
                "rows": [{"cells": list(r.cells), "confidence": r.confidence, "label": False} for r in table_rows],
                "columnBounds": bounds,
            }
        ],
        column_bounds=bounds,
        body_left=min(w.x for row in table.rows for w in row),
        validation=validation,
        skipped=[],
        warnings=[issue.message for issue in validation],
        word_count=len(words),
    )
