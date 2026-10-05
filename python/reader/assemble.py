"""
Pair words into the general reading of a page: the tables printed on it.

This is the reader for any document that is not one of the special ones the user
names — the lottery's papers (`reader.lottery`) and bank statements
(`reader.bank`) are read by readers of their own. It does not guess that a page
is one of those, and a page with no table in it is an empty table.

No image work lives here, so the same builders serve a PDF's own text and the
recogniser's words alike.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .boxes import WordBox, group_into_lines
from .columns import ColumnGuide, read_column_tables
from .validate import ValidationIssue


@dataclass(slots=True)
class OcrResult:
    #: Always `table`: every page is read as the table it holds, and nothing
    #: here says what sort of document that is.
    kind: str
    title: str | None
    headers: list[str]
    table_rows: list[Any]
    tables: list[dict]
    column_bounds: list[float] | None
    validation: list[ValidationIssue]
    skipped: list[Any]
    warnings: list[str] = field(default_factory=list)
    word_count: int = 0
    #: Left edge of the rows the page's table was read from, where it has one.
    #: `column_bounds[0]` is the page's own edge and says nothing about where
    #: the items start; this does, and `skipped.refine` judges against it.
    body_left: float | None = None


def clean_title(raw: str) -> str:
    text = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", raw)
    return re.sub(r"\s+", " ", text).strip()


def invoice_heading(text: str) -> str | None:
    """A street address or a date range on the same line as the title is not the title."""
    match = re.search(r"\binvoice\s*#?\s*:?\s*(\d{4,})", text, re.I)
    if not match:
        return None
    heading = f"Invoice # {match.group(1)}"
    if len(text) <= len(heading) + 4:
        return None
    return heading


TITLE_WORDS = re.compile(r"\b(?:invoice|statement|bill|order)\b", re.I)


def extract_receipt_title(words: Sequence[WordBox]) -> str | None:
    """Printed header/title text taken straight from the page's words."""
    if not words:
        return None
    lines = group_into_lines(words)
    scored: list[tuple[str, float]] = []

    for line in lines[:16]:
        raw_text = " ".join(word.text for word in line).strip()
        text = clean_title(raw_text)
        if not text or len(text) < 3:
            continue
        if re.match(r"^https?://|www\.|\\.com\b", text, re.I):
            continue
        if re.match(r"^retailer\b|^store\b|^terminal\b", text, re.I):
            continue
        if re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}", text):
            continue

        score = 0.0
        if TITLE_WORDS.search(text):
            score += 50
        average_height = sum(word.height for word in line) / len(line)
        score += min(30.0, average_height)
        if text == text.upper() and re.search(r"[A-Z]", text):
            score += 15
        if score > 30:
            scored.append((text, score))

    if not scored:
        return None
    # Highest score first, keeping the printed order among equals.
    scored.sort(key=lambda pair: -pair[1])
    best = scored[0][0]
    return invoice_heading(best) or best


def assemble_receipt(
    words: Sequence[WordBox],
    row_overlap_ratio: float = 0.5,
    guide: ColumnGuide | None = None,
) -> OcrResult:
    """The tables a page prints, as a column reader finds them."""
    tables = read_column_tables(words, row_overlap_ratio, guide)
    table = tables[0] if tables else None

    warnings: list[str] = []
    if not words:
        warnings.append("No words were read. The page may be blank after watermark removal.")

    return OcrResult(
        kind="table",
        title=extract_receipt_title(words),
        headers=list(table.headers) if table is not None else [],
        table_rows=table.rows if table is not None else [],
        tables=[
            {
                **({"title": each.title} if each.title else {}),
                "headers": list(each.headers),
                "rows": [
                    {"cells": list(row.cells), "confidence": row.confidence, "label": row.label}
                    for row in each.rows
                ],
                "columnBounds": list(each.bounds),
            }
            for each in tables
        ],
        column_bounds=list(table.bounds) if table is not None else None,
        body_left=table.body_left if table is not None else None,
        validation=[],
        # Only the column reader keeps a log.
        skipped=sorted(
            (entry for each in tables for entry in each.skipped), key=lambda entry: entry.y
        ),
        warnings=warnings,
        word_count=len(words),
    )


def row_cells(result: OcrResult) -> list[list[str]]:
    """Each row's cells, left to right in the order the page prints its columns."""
    return [list(row.cells) for row in result.table_rows]
