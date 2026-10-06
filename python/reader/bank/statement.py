"""
A bank statement in, the reading out.

Pages come from the same places as for any document — a PDF's text layer, or
the recogniser for a scan or a photograph — and leave in the same shape, so the
viewer, the edits and the export need nothing of their own.

The middle reads no word for its meaning. Rows are the lines that carry a date
and a figure; columns are where those rows' ink gathers; the titles are copied
from above them as printed; and which column is the running balance, and which
way each other column moves it, is found by the arithmetic. The user chose
"bank statement"; nothing else is assumed about what the page says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from ..assemble import OcrResult
from ..boxes import WordBox, median
from ..columns import SkippedLine, TableRow
from ..document import PageReading, Recogniser, _recognised, load_image_pixels, looks_like_pdf
from ..pdf_text import DPI, read_pdf, render_page, text_layer_is_usable
from .checks import Audit, Roles, Txn, audit, infer_roles, txn_from
from .columns import Column, Group, cells_of, clean, induce_columns, make_groups, usable
from .fields import Field, read_fields
from .labels import read_labels
from .money import format_money, is_money, parse_money

TITLE = "Bank statement"


@dataclass(slots=True)
class _Page:
    number: int
    width: int
    height: int
    reader: str
    words: list[WordBox]


def _pages(data: bytes, recognise: Recogniser | None, dpi: int) -> list[_Page]:
    if not looks_like_pdf(data):
        pixels = load_image_pixels(data)
        words = _recognised(recognise, pixels) if recognise is not None else []
        return [
            _Page(1, int(pixels.shape[1]), int(pixels.shape[0]), "recogniser" if recognise else "none", words)
        ]
    pages: list[_Page] = []
    for page in read_pdf(data, dpi):
        if text_layer_is_usable(page.words):
            words, reader = page.words, "pdf text"
        elif recognise is not None:
            words = _recognised(recognise, render_page(data, page.number, dpi))
            reader = "recogniser"
        else:
            words, reader = [], "none"
        pages.append(_Page(page.number, page.width, page.height, reader, words))
    return pages


def _first_figure(cell: str) -> Decimal | None:
    for token in cell.split():
        if is_money(token):
            return parse_money(token)
    return None


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


@dataclass(slots=True)
class _Row:
    page: int
    group: Group
    cells: list[str]
    values: list[Decimal | None]  # by money column


def read_statement(
    data: bytes,
    recognise: Recogniser | None = None,
    dpi: int = DPI,
) -> tuple[list[PageReading], dict[str, Any]]:
    """Every page of a statement, read, and a summary of what the statement says."""
    return read_pages(_pages(data, recognise, dpi))


def read_pages(pages: Sequence[_Page]) -> tuple[list[PageReading], dict[str, Any]]:
    """The same, from pages whose words are already in hand."""
    heights = [w.height for p in pages for w in usable(p.words)]
    height = median(heights) or 12

    # Pass one finds the columns and, from them, the titles. A title line that
    # sat close over the first row would have been gathered into it, so the rows
    # are cut again without the title words and the columns learned again.
    groups = {p.number: make_groups(p.words, height, p.number) for p in pages}
    columns = induce_columns([g for gs in groups.values() for g in gs if g.is_row], height)
    titles: dict[int, tuple[list[str], list[WordBox]]] = {}
    for page in pages:
        rows_here = [g for g in groups[page.number] if g.is_row]
        if rows_here and columns:
            found = read_labels(page.words, columns, min(g.top for g in rows_here), height)
            if found:
                titles[page.number] = found
    if titles:
        title_ids = {id(w) for _, words in titles.values() for w in words}
        groups = {
            p.number: make_groups([w for w in p.words if id(w) not in title_ids], height, p.number)
            for p in pages
        }
        columns = induce_columns([g for gs in groups.values() for g in gs if g.is_row], height)
    else:
        title_ids = set()

    money = [i for i, c in enumerate(columns) if c.kind == "money"]
    date_column = next((i for i, c in enumerate(columns) if c.kind == "date"), None)

    labels: list[str] = []
    for page in pages:
        if page.number in titles:
            labels = titles[page.number][0]
            break
    if len(labels) != len(columns):
        labels = [f"Column {i + 1}" for i in range(len(columns))]
    labels = [label or f"Column {i + 1}" for i, label in enumerate(labels)]

    rows: list[_Row] = []
    leftovers: list[Group] = []
    for page in pages:
        for group in groups[page.number]:
            if group.is_row and columns:
                cells = cells_of(group, columns, height)
                rows.append(_Row(page.number, group, cells, [_first_figure(cells[i]) for i in money]))
            else:
                leftovers.append(group)
    rows.sort(key=lambda r: (r.page, r.group.top))

    roles = infer_roles([r.values for r in rows])

    # A line with no readable date that still carries a balance and an amount
    # is a row whose date the recogniser lost; the running balance needs it.
    if roles is not None:
        kept: list[Group] = []
        for group in leftovers:
            cells = cells_of(group, columns, height)
            values = [_first_figure(cells[i]) for i in money]
            others = [v for c, v in enumerate(values) if c != roles.balance and v is not None]
            if values[roles.balance] is not None and others:
                rows.append(_Row(group.page, group, cells, values))
            else:
                kept.append(group)
        leftovers = kept
        rows.sort(key=lambda r: (r.page, r.group.top))

    # What the page prints beside its table: the head of a statement is fields,
    # and a field is by definition not a row, so the rows go first and the
    # fields are read from what they leave.
    taken = {id(w) for r in rows for w in r.group.words} | title_ids
    details: list[Field] = []
    for page in pages:
        details += read_fields([w for w in page.words if id(w) not in taken], height, page.number)

    readings: list[PageReading] = []
    txns: list[Txn] = []
    per_page: dict[int, list[_Row]] = {p.number: [] for p in pages}
    for row in rows:
        per_page[row.page].append(row)
    for page in pages:
        for index, row in enumerate(per_page[page.number]):
            if roles is not None:
                txns.append(txn_from(page.number, index, row.values, roles))

    # What the rows add up to, by column, and what the page prints under them.
    sums: dict[int, Decimal] = {}
    for c in range(len(money)):
        total = sum((abs(r.values[c]) for r in rows if r.values[c] is not None), Decimal(0))
        sums[c] = total
    printed: dict[int, Decimal] = {}

    skipped_by_page: dict[int, list[SkippedLine]] = {p.number: [] for p in pages}
    seen: dict[str, set[int]] = {}
    body_left = {n: min((r.group.words and min(w.x for w in r.group.words)) for r in rs) for n, rs in per_page.items() if rs}
    for group in leftovers:
        if title_ids and all(id(w) in title_ids for w in group.words):
            continue
        text = " ".join(w.text.strip() for w in sorted(group.words, key=lambda w: (round(w.centre_y / 4), w.x)))
        seen.setdefault(_normal(text), set()).add(group.page)

    for group in leftovers:
        if title_ids and all(id(w) in title_ids for w in group.words):
            continue
        text = " ".join(w.text.strip() for w in sorted(group.words, key=lambda w: (round(w.centre_y / 4), w.x)))
        here = per_page[group.page]
        below = bool(here) and group.top > max(r.group.bottom for r in here)
        above = bool(here) and group.bottom < min(r.group.top for r in here)
        cells = cells_of(group, columns, height) if columns else []
        values = [_first_figure(cells[i]) for i in money] if columns else []
        figured = {c: v for c, v in enumerate(values) if v is not None}
        balance_blank = roles is None or roles.balance not in figured
        left = min(w.x for w in group.words)
        if below and figured and balance_blank:
            reason = "summary"
            for c, v in figured.items():
                if roles is None or c != roles.balance:
                    printed.setdefault(c, v)
        elif len(seen.get(_normal(text), ())) > 1:
            reason = "page-field"
        elif page_left(body_left, group.page) is not None and left < page_left(body_left, group.page) - height:
            reason = "margin"
        elif above or below or not here:
            reason = "furniture"
        else:
            reason = "unplaced"
        confidence = sum(w.confidence for w in group.words) / len(group.words)
        skipped_by_page[group.page].append(SkippedLine(reason, text, confidence, group.top, left))

    report = audit(txns, roles, printed, [labels[i] for i in money], sums, pages[0].number if pages else 1)

    for page in pages:
        here = per_page[page.number]
        result = _result(columns, labels, here, skipped_by_page[page.number], len(page.words))
        if not here:
            result.warnings.append(
                "No rows were found on this page. A bank statement's rows each carry a date and an amount."
                if page.reader != "none"
                else "This page is a scan and no recogniser is available to read it."
            )
        for issue in report.issues.get(page.number, []):
            result.validation.append(issue)
            result.warnings.append(issue.message)
        readings.append(
            PageReading(
                number=page.number,
                width=page.width,
                height=page.height,
                reader=page.reader,
                result=result,
                words=list(page.words),
            )
        )
    return readings, _summary(rows, date_column, money, roles, report, details)


def page_left(body_left: dict[int, float], page: int) -> float | None:
    return body_left.get(page)


def _result(
    columns: Sequence[Column],
    labels: list[str],
    rows: Sequence[_Row],
    skipped: list[SkippedLine],
    word_count: int,
) -> OcrResult:
    table_rows = [
        TableRow(cells=r.cells, confidence=sum(w.confidence for w in r.group.words) / len(r.group.words))
        for r in rows
    ]
    bounds = [c.left for c in columns] or None
    return OcrResult(
        kind="table",
        title=TITLE,
        headers=list(labels) if columns else [],
        table_rows=table_rows,
        tables=[
            {
                "headers": list(labels),
                "rows": [{"cells": list(r.cells), "confidence": r.confidence, "label": False} for r in table_rows],
                "columnBounds": bounds or [],
            }
        ]
        if columns
        else [],
        column_bounds=bounds,
        validation=[],
        skipped=sorted(skipped, key=lambda s: s.y),
        warnings=[],
        word_count=word_count,
        body_left=min((min(w.x for w in r.group.words) for r in rows), default=None),
    )


def _summary(
    rows: Sequence[_Row],
    date_column: int | None,
    money_columns: Sequence[int],
    roles: Roles | None,
    report: Audit,
    details: Sequence[Field],
) -> dict[str, Any]:
    def money(value: Decimal | None) -> str | None:
        return format_money(value) if value is not None else None

    dates = [r.cells[date_column] for r in rows if date_column is not None and r.cells[date_column]]
    known = roles is not None
    return {
        "transactions": len(rows),
        # The fields printed beside the rows, as printed. A head repeated on
        # every page is one field to whoever reads it, so it is said once.
        "details": _once([{"label": f.label, "value": f.value, "page": f.page} for f in details]),
        "firstDate": dates[0] if dates else None,
        "lastDate": dates[-1] if dates else None,
        # Which way the rows run, so a date range reads earliest to latest
        # whichever end of the month the statement starts at.
        "newestFirst": roles.descending if roles else None,
        # What the arithmetic showed each column to be, as an index into the
        # row's cells. The reading is the same either way; this is only so the
        # table can draw a balance as a balance and a blank credit as a blank.
        "columns": _roles_by_column(date_column, money_columns, roles),
        "debits": money(report.money_out) if known else None,
        "credits": money(report.money_in) if known else None,
        "openingBalance": money(report.opening),
        "closingBalance": money(report.closing),
        "balanceCheck": report.balance,
        "balanceLinksChecked": report.checked,
        "balanceLinksBroken": report.broken,
        "stated": {
            "transactions": None,
            "debits": money(report.stated_out),
            "credits": money(report.stated_in),
        },
    }


def _roles_by_column(
    date_column: int | None,
    money_columns: Sequence[int],
    roles: Roles | None,
) -> dict[str, Any]:
    """
    What each column does, by its place among the row's cells.

    `Roles` counts in money columns, since the arithmetic only ever sees those;
    the page draws whole rows, so the indices are mapped back here and nowhere
    else. Empty where no column could be shown to be a running balance: then
    nothing is known, and saying so is better than a guess.
    """
    if roles is None:
        return {"date": date_column, "balance": None, "credits": [], "debits": []}
    return {
        "date": date_column,
        "balance": money_columns[roles.balance],
        "credits": sorted(money_columns[c] for c, sign in roles.signs.items() if sign > 0),
        "debits": sorted(money_columns[c] for c, sign in roles.signs.items() if sign < 0),
    }


def _once(details: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fields in printed order, each said once however often it is printed."""
    seen: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for detail in details:
        key = (detail["label"], detail["value"])
        if key not in seen:
            seen.add(key)
            kept.append(detail)
    return kept
