"""
A bank statement: dated lines with a debit and a credit, not invoice items.

The layout is the one a bank's web page prints — titles with a sort mark, amounts
set flush right under them, the check link above the line and its number below,
and a pager under the table that counts rows without being one.

    .venv/bin/python -m pytest python/tests/test_ledger.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.assemble import assemble_receipt, row_cells  # noqa: E402
from reader.boxes import WordBox  # noqa: E402

H = 36
CHAR = 13


def word(text: str, x: float, y: float) -> WordBox:
    return WordBox(text=text, x=x, y=y, width=len(text) * CHAR, height=H)


def right(text: str, edge: float, y: float) -> WordBox:
    return word(text, edge - len(text) * CHAR, y)


def statement() -> list[WordBox]:
    words = [
        word("Date", 160, 96),
        word("Check/Ref", 369, 96),
        word("#", 500, 96),
        word("Description", 560, 96),
        word("Debit", 1582, 96),
        word("Credit", 1767, 96),
        word("Balance", 1941, 96),
    ]
    # (date, link, number, description, debit, credit, balance)
    lines = [
        ("09/16/2026", "View Check", "1006", "Check (Regular Inclearings)", "$2,025.50", "", "$98,830.78"),
        ("09/16/2026", "View Check", "1004", "Check (Regular Inclearings)", "$1,328.50", "", "$100,856.28"),
        ("09/16/2026", "View Deposit", "", "RDN - DDA Deposit", "", "$45,308.69", "$170,991.18"),
        ("09/15/2026", "", "", "ACH Debit, Worldwide PAYMENTS, CCD", "$3,150.69", "", "$92,167.29"),
    ]
    y = 150
    for date, link, number, description, debit, credit, balance in lines:
        if link and number:
            words.append(word(link, 369, y))
            y += 20
        words.append(word(date, 160, y))
        if link and not number:
            words.append(word(link, 369, y))
        words.append(word(description, 560, y))
        for amount, edge in ((debit, 1650), (credit, 1835), (balance, 2040)):
            if amount:
                words.append(right(amount, edge, y))
        if number:
            words.append(word(number, 369, y + 20))
        y += 90
    words.append(word("Viewing", 160, y))
    words.append(word("1 - 4 of 4 transactions", 280, y))
    words.append(right("($6,504.69)", 1650, y))
    words.append(right("$45,308.69", 1835, y))
    return words


def test_every_dated_line_is_a_row():
    result = assemble_receipt(statement(), 0.5, None)
    assert result.kind == "table"
    assert len(row_cells(result)) == 4


def test_amounts_sit_under_the_column_they_are_flush_with():
    cells = row_cells(assemble_receipt(statement(), 0.5, None))
    assert [c[3:] for c in cells] == [
        ["$2,025.50", "", "$98,830.78"],
        ["$1,328.50", "", "$100,856.28"],
        ["", "$45,308.69", "$170,991.18"],
        ["$3,150.69", "", "$92,167.29"],
    ]


def test_a_link_above_a_line_belongs_to_that_line_not_the_one_before():
    cells = row_cells(assemble_receipt(statement(), 0.5, None))
    assert [c[1] for c in cells[:3]] == ["View Check 1006", "View Check 1004", "View Deposit"]


def test_a_description_containing_a_totals_word_is_still_a_row():
    cells = row_cells(assemble_receipt(statement(), 0.5, None))
    assert "PAYMENTS" in cells[3][2]


def test_the_pager_is_not_a_row():
    result = assemble_receipt(statement(), 0.5, None)
    assert not any("Viewing" in " ".join(c) for c in row_cells(result))


def test_a_totals_block_under_the_items_is_not_rows():
    words = statement()
    y = 900
    for left, count, key, figure in (
        ("Zebras:", "1143", "Quokka Fee:", "$45,812.09"),
        ("Mango Lots:", "440", "Plinth:", "$0.00"),
    ):
        words += [word(left, 160, y), word(count, 560, y), word(key, 1400, y), right(figure, 2040, y)]
        y += 36
    cells = row_cells(assemble_receipt(words, 0.5, None))
    # Labels no vocabulary knows: only where the lines sit and what they hold.
    assert not any(("Zebras" in " ".join(c)) or ("Mango" in " ".join(c)) for c in cells)
    assert len(cells) == 4
