"""
Check a reading against the page's own arithmetic.

What matters for a financial document is not a headline accuracy number but
whether the reading can say *which* rows it got wrong, and these pages restate
their own contents — a totals row, a footer count, a figure printed twice — so
a misread digit contradicts something else on the same page.

Nothing here knows what a column or a row is called. A totals row is the row
whose figures equal the others' sums; a repeated figure is one under a label
that is the same words however spelled. Nothing here corrects a value that was
read; it only says where to look.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass
from typing import Sequence



@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    rows: list[int]


def cents(text: str) -> int | None:
    """Money as an integer number of cents, or None when the text is not an amount."""
    token = re.sub(r"[$,\s]", "", text.strip())
    credit = bool(re.search(r"[cC]$", token))
    body = token[:-1] if credit else token
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", body):
        return None
    value = math.floor(float(body) * 100 + 0.5)
    if not math.isfinite(value):
        return None
    return -value if credit else value


def money(value: int) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    return f"{sign}{magnitude // 100}.{magnitude % 100:02d}"


def count(text: str) -> int | None:
    """A count column, or None when the reader mangled it past use."""
    token = text.strip()
    if not re.fullmatch(r"\d{1,3}", token):
        return None
    return int(token)


def _number(text: str) -> int | None:
    """A cell's figure as a whole number of hundredths, or None where it is not one."""
    token = re.sub(r"[$,\s]", "", text.strip())
    credit = bool(re.search(r"[cC]$", token))
    body = token[:-1] if credit else token
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", body):
        return None
    value = math.floor(float(body) * 100 + 0.5)
    return -value if credit else value


def _column_title(labels: Sequence[str], column: int) -> str:
    return labels[column] if column < len(labels) and labels[column] else f"column {column + 1}"


def find_totals_row(cells: Sequence[Sequence[str]]) -> tuple[int, list[int]] | None:
    """
    The row that is the table's own totals, found by arithmetic.

    A line of totals restates each column's sum, so it is the row whose figures
    equal the sums of the other rows' in at least two columns. Nothing about how
    the row is labelled, or whether it is labelled at all, is looked at.
    """
    if len(cells) < 4:
        return None
    width = max(len(row) for row in cells)
    values = [[_number(row[c]) if c < len(row) else None for c in range(width)] for row in cells]
    best: tuple[int, list[int]] | None = None
    for r in range(len(cells)):
        matched = []
        for c in range(width):
            stated = values[r][c]
            others = [values[i][c] for i in range(len(cells)) if i != r]
            if stated is None or not others or any(v is None for v in others if v is not None) or sum(v is not None for v in others) < len(others) * 0.6:
                continue
            if stated == sum(v for v in others if v is not None):
                matched.append(c)
        if len(matched) >= 2 and (best is None or len(matched) > len(best[1])):
            best = (r, matched)
    return best


def validate_totals(cells: Sequence[Sequence[str]], labels: Sequence[str]) -> list[ValidationIssue]:
    """The totals row, where there is one, restates each column's sum: say where it does not."""
    found = find_totals_row(cells)
    if found is None:
        return []
    row, _ = found
    width = max(len(r) for r in cells)
    issues: list[ValidationIssue] = []
    for c in range(width):
        stated = _number(cells[row][c]) if c < len(cells[row]) else None
        column = [(i, _number(r[c]) if c < len(r) else None) for i, r in enumerate(cells) if i != row]
        figures = [v for _, v in column if v is not None]
        # A column that is not made of figures has no sum to restate.
        if stated is None or len(figures) < len(column) * 0.6 or not figures:
            continue
        unread = [i for i, v in column if v is None]
        if sum(figures) == stated:
            continue
        whole = all(v % 100 == 0 for v in [*figures, stated])
        show = (lambda v: str(v // 100)) if whole else money
        issues.append(
            ValidationIssue(
                code="table-totals",
                message=(
                    f"{_column_title(labels, c).capitalize()} adds up to {show(sum(figures))}, but the "
                    f"totals row says {show(stated)}."
                    + (f" {len(unread)} row{'' if len(unread) == 1 else 's'} had no readable figure." if unread else "")
                ),
                rows=unread if unread else [row],
            )
        )
    return issues


def validate_count(rows: int, stated: int | None) -> list[ValidationIssue]:
    """
    A count a page states under its rows, held against the rows read.

    A figure at the foot of a page might be anything — a page number, a store —
    so it is taken for the count only when it could be one: not far from the
    number of rows.
    """
    if stated is None or rows == stated or not rows * 0.5 <= stated <= max(rows * 3, 3):
        return []
    missing = stated - rows
    tail = f" — {missing} row{' is' if missing == 1 else 's are'} missing" if missing > 0 else ""
    return [
        ValidationIssue(
            code="table-count",
            message=f"The page states {stated} but {rows} rows were read{tail}.",
            rows=[],
        )
    ]


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _similar(a: str, b: str) -> bool:
    """Two labels that are the same words, however the reader spelled them."""
    ka, kb = _key(a), _key(b)
    if min(len(ka), len(kb)) < 6:
        return False
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.82


def _one_digit_apart(x: int, y: int) -> bool:
    """Two amounts that differ in exactly one digit: the mark of a digit misread."""
    a, b = str(abs(x)), str(abs(y))
    # Under ten dollars, one digit apart is nothing: `0.04` and `0.00` differ by one.
    return len(a) >= 4 and len(a) == len(b) and sum(c != d for c, d in zip(a, b)) == 1


def validate_pairs(pairs: Sequence[tuple[str, str]]) -> list[ValidationIssue]:
    """
    A list of figures held to its own arithmetic, without knowing what any is called.

    Two things a page of figures says about itself:

    * a figure printed twice — the same label, the same words however spelled —
      is the same figure, so two that differ in a single digit are one misread;
    * a line that is the total of those above it adds them up, so a total one
      digit away from their sum is a misread of the total or of an addend.

    A repeated label whose figures differ a lot is a different figure that
    happens to share a name, and is left alone.
    """
    issues: list[ValidationIssue] = []
    amounts = [_number(value) for _, value in pairs]

    for first in range(len(pairs)):
        for second in range(first + 1, len(pairs)):
            top, bottom = amounts[first], amounts[second]
            if top is None or bottom is None or not _similar(pairs[first][0], pairs[second][0]):
                continue
            if top != bottom and _one_digit_apart(top, bottom):
                issues.append(
                    ValidationIssue(
                        code="table-repeated",
                        message=(
                            f"{pairs[first][0]} is {money(top)} at the top of the page but "
                            f"{pairs[second][0]} is {money(bottom)} further down; one of them "
                            "was misread."
                        ),
                        rows=[first, second],
                    )
                )

    reported: set[int] = set()
    for start in range(0, 3):
        for end in range(start + 3, min(len(pairs), 15)):
            addends = amounts[start:end]
            total = amounts[end]
            if end in reported or total is None or any(value is None for value in addends):
                continue
            added = sum(value or 0 for value in addends)
            if added > 0 and added != total and _one_digit_apart(added, total):
                reported.add(end)
                issues.append(
                    ValidationIssue(
                        code="table-sum",
                        message=(
                            f"The {end - start} lines above {pairs[end][0]} add up to "
                            f"{money(added)}, but it says {money(total)}."
                        ),
                        rows=list(range(start, end + 1)),
                    )
                )
    return issues
