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


def _invoice(label: str) -> list[WordBox]:
    words = [
        word("MFG", 64, 96), word("ITEM", 302, 96), word("DESCRIPTION", 568, 96),
        word("ORDER", 1114, 96), word("PRICE", 1400, 96),
    ]
    words += [word(label, 64, 150), word("WEB6842382", 302, 150)]
    for n, (mfg, item, text, qty, price) in enumerate(
        [("144732", "556788", "MUSKETEERS MULTI", "1", "$273.60"),
         ("718128", "507267", "BERRY EXTRA", "5", "$403.92"),
         ("768123", "633383", "RASPBERRY EXTRA", "2", "$403.92"),
         ("783188", "743029", "STRENGTH CHERRY", "1", "$403.92")]
    ):
        y = 186 + n * 36
        words += [word(mfg, 64, y), word(item, 302, y), word(text, 568, y), word(qty, 1114, y), word(price, 1400, y)]
    return words


def test_a_field_label_is_not_the_start_of_an_item_even_when_misread():
    # `P.0.:` is what the recogniser makes of `P.O.:` as often as not.
    for label in ("P.O.:", "P.0.:"):
        cells = row_cells(assemble_receipt(_invoice(label), 0.5, None))
        assert cells and cells[0][0] == "144732", label


def _order() -> list[WordBox]:
    words = [word("Item", 160, 96), word("Qty", 1100, 96), word("Unit", 1400, 96), word("price", 1480, 96), word("Total", 1800, 96)]
    for n, (name, qty, unit, total) in enumerate(
        [("RIP IT RED ZONE 16OZ CAN", "1.0000", "$102.00", "$102.00"),
         ("Pack water", "3.0000", "$94.33", "$283.00"),
         ("LD BLUE 100 BOX", "1.0000", "$101.00", "$101.00")]
    ):
        y = 150 + n * 40
        words += [word(name, 160, y), right(qty, 1230, y), right(unit, 1580, y), right(total, 1950, y)]
    y = 400
    for key, amount in (("Subtotal", "$486.00"), ("Tax", "$14.00"), ("Total paid", "$500.00")):
        words += [word(key, 160, y), right(amount, 1950, y)]
        y += 36
    return words


def test_an_order_with_decimal_quantities_and_no_codes_reads_its_lines():
    cells = row_cells(assemble_receipt(_order(), 0.5, None))
    assert [c[0] for c in cells] == ["RIP IT RED ZONE 16OZ CAN", "Pack water", "LD BLUE 100 BOX"]
    assert [c for c in cells[1] if c] == ["Pack water", "3.0000", "$94.33", "$283.00"]



def test_a_date_with_a_letter_for_a_digit_is_still_a_date():
    from reader.dates import restore_date_digits, settled_date

    # Whichever digit the recogniser swaps for a letter, not only 0 and 1.
    for misread, date in (
        ("o2/28/26", "02/28/26"),
        ("O2/2l/26", "02/21/26"),
        ("O5/1S/26", "05/15/26"),
        ("o9/2B/26", "09/28/26"),
        ("l2/Z1/26", "12/21/26"),
        ("o7/2g/26", "07/29/26"),
        # A slash read as a character as well.
        ("o2/28726", "02/28/26"),
        ("O5/1S726", "05/15/26"),
    ):
        assert settled_date(misread) == date, misread
    assert restore_date_digits("O5/1S/26") == "05/15/26"


def test_text_with_slashes_is_not_turned_into_a_date():
    from reader.dates import restore_date_digits, settled_date

    for text in ("LOTTO/OIL/XX", "SO/ZZ/TT", "lo/ll/ll", "IS/OS/BS", "ABC/DEF123", "TOLL"):
        assert restore_date_digits(text) == text, text
        assert settled_date(text) is None, text
