"""
The sectioned bank-statement reader.

A statement of this shape balances by a box at its head, not by a running
balance beside each row, and prints its transactions under three banners —
CHECKS, OTHER DEBITS, CREDITS. These tests lay a page out the way such a
statement prints one and hold the reading to the box's own arithmetic.

    .venv/bin/python -m pytest python/tests/test_sectioned.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.bank.sectioned import looks_sectioned, read_sectioned  # noqa: E402
from reader.bank.statement import _Page  # noqa: E402
from reader.boxes import WordBox  # noqa: E402
from reader.export import ExportPage, extra_tables, to_csv, to_document_json  # noqa: E402

H = 30


def w(text: str, x: float, y: float) -> WordBox:
    return WordBox(text=text, x=x, y=y, width=max(1.0, len(text) * 13), height=H)


def r(text: str, edge: float, y: float) -> WordBox:
    """A word whose right edge sits at `edge`: a figure in a right-aligned column."""
    return w(text, edge - len(text) * 13, y)


# A head whose box states the four figures the statement balances by, laid out
# as the statement prints them: label then figure, two columns of them.
def head(y: float) -> list[WordBox]:
    out: list[WordBox] = []
    out += [w("RENASANT", 120, y - 120), w("BANK", 300, y - 120)]
    out += [w("ACCOUNT", 120, y - 80), w("NUMBER", 260, y - 80), w("0019002021", 460, y - 80)]
    out += [w("APRIL", 900, y - 90), w("30,", 1010, y - 90), w("2026:", 1080, y - 90), w("LAST", 1200, y - 90), w("STATEMENT", 1300, y - 90)]
    out += [w("MAY", 900, y - 60), w("31,", 1000, y - 60), w("2026:", 1070, y - 60), w("THIS", 1200, y - 60), w("STATEMENT", 1300, y - 60)]
    out += [w("*****", 120, y), w("COMMERCIAL", 400, y), w("CHECKING", 620, y), w("-", 760, y), w("SUMMARY", 820, y), w("*****", 1100, y)]
    out += [w("PREVIOUS", 900, y + 40), w("BALANCE", 1060, y + 40), r("$444,383.67", 1500, y + 40)]
    out += [w("ADDITIONS", 900, y + 80), w("+", 1200, y + 80), r("1,113,284.17", 1500, y + 80)]
    out += [w("SUBTRACTIONS", 900, y + 120), w("-", 1200, y + 120), r("1,071,149.46", 1500, y + 120)]
    out += [w("ENDING", 900, y + 160), w("BALANCE", 1040, y + 160), r("$486,518.38", 1500, y + 160)]
    return out


# Two check columns side by side, then OTHER DEBITS, then CREDITS. The figures
# are made up but add up: the checks and debits to the subtractions, the credits
# to the additions, and the box to itself.
CHECKS_ROWS = [
    ("11391", "", "05-05", "510.25", "11458", "", "05-12", "433.55"),
    ("11415", "*", "05-01", "2,261.61", "11459", "", "05-12", "343.31"),
]
# 510.25 + 433.55 + 2,261.61 + 343.31 = 3,548.72
DEBITS_ROWS = [
    ("05-01", "#WITHDRAWAL", "4,618.46"),
    ("05-04", "#WITHDRAWAL", "1,000.00"),
]
# 4,618.46 + 1,000.00 = 5,618.46  → subtractions = 3,548.72 + 5,618.46 = 9,167.18
CREDITS_ROWS = [
    ("05-01", "EXPRESS DEPOSIT", "7,000.00"),
    ("05-04", "DIRECT DEPOSIT", "2,167.18"),
]
# additions = 9,167.18


def checks_page() -> list[WordBox]:
    words = head(400)
    words += [w("*****", 120, 620), w("CHECKS", 600, 620), w("*****", 1100, 620)]
    words += [w("NUMBER", 120, 660), w("DATE", 400, 660), r("AMOUNT", 760, 660),
              w("NUMBER", 900, 660), w("DATE", 1180, 660), r("AMOUNT", 1540, 660)]
    y = 720
    for num1, star1, date1, amt1, num2, star2, date2, amt2 in CHECKS_ROWS:
        words.append(w(num1, 120, y))
        if star1:
            words.append(w(star1, 260, y))
        words.append(w(date1, 400, y))
        words.append(r(amt1, 760, y))
        words.append(w(num2, 900, y))
        words.append(w(date2, 1180, y))
        words.append(r(amt2, 1540, y))
        y += 40
    # The legend the statement prints after the last check, sharing a line here.
    words += [w("*", 900, y), w("SKIP", 940, y), w("IN", 1040, y), w("CHECK", 1100, y), w("SEQUENCE", 1220, y)]
    return words


def _list_page(banner: str, rows) -> list[WordBox]:
    words = [w("*****", 120, 400), w(banner, 600, 400), w("*****", 1100, 400)]
    words += [w("DATE", 120, 440), w("DESCRIPTION", 400, 440), r("AMOUNT", 1540, 440)]
    y = 500
    for date, desc, amount in rows:
        words.append(w(date, 120, y))
        for index, token in enumerate(desc.split()):
            words.append(w(token, 400 + index * 180, y))
        words.append(r(amount, 1540, y))
        y += 70
    return words


def statement_pages() -> list[_Page]:
    return [
        _Page(1, 1700, 2200, "bitmap", checks_page()),
        _Page(2, 1700, 2200, "bitmap", _list_page("OTHER DEBITS", DEBITS_ROWS)),
        _Page(3, 1700, 2200, "bitmap", _list_page("CREDITS", CREDITS_ROWS)),
    ]


# --------------------------------------------------------------------------- #
# Which reader a statement is for                                              #
# --------------------------------------------------------------------------- #


def test_a_statement_with_section_banners_is_read_by_this_reader():
    assert looks_sectioned(statement_pages())


def test_a_ledger_statement_is_not():
    # A page of rows with no banners and no box is not this shape.
    rows = [w("09/30/2026", 120, 100 + i * 40) for i in range(5)]
    rows += [r("92,167.29", 1500, 100 + i * 40) for i in range(5)]
    assert not looks_sectioned([_Page(1, 1700, 2200, "pdf text", rows)])


# --------------------------------------------------------------------------- #
# The rows of each section                                                     #
# --------------------------------------------------------------------------- #


def test_each_section_is_read_as_its_own_table():
    readings, summary = read_sectioned(statement_pages())
    assert summary["counts"] == {"Checks": 4, "Other Debits": 2, "Credits": 2}
    assert summary["transactions"] == 8


def test_two_checks_on_one_line_are_two_rows_and_the_star_is_kept():
    readings, _ = read_sectioned(statement_pages())
    checks = readings[0].result.tables[1]
    numbers = [row["cells"][0] for row in checks["rows"]]
    assert numbers == ["11391", "11458", "11415 *", "11459"]


def test_the_skip_legend_does_not_become_a_check():
    readings, summary = read_sectioned(statement_pages())
    # Four checks, not five: the `* SKIP IN CHECK SEQUENCE` line is not a row.
    assert summary["counts"]["Checks"] == 4


def test_a_continuation_line_joins_the_row_above_it():
    rows = [("05-01", "#WITHDRAWAL", "4,618.46")]
    page = _list_page("OTHER DEBITS", rows)
    # A second line under the row, with no date: its description continues.
    page += [w("PAYROLLTAX", 400, 540), w("TAX", 580, 540), w("DEBIT", 660, 540)]
    readings, summary = read_sectioned([_Page(1, 1700, 2200, "bitmap", head(400) + page)])
    assert summary["counts"]["Other Debits"] == 1
    debit = readings[0].result.tables[1]["rows"][0]
    assert "PAYROLLTAX TAX DEBIT" in debit["cells"][1]


# --------------------------------------------------------------------------- #
# What the statement balances by                                              #
# --------------------------------------------------------------------------- #


def test_the_box_figures_are_read_as_printed():
    _, summary = read_sectioned(statement_pages())
    assert summary["openingBalance"] == "444,383.67"
    assert summary["closingBalance"] == "486,518.38"
    assert summary["stated"]["credits"] == "1,113,284.17"
    assert summary["stated"]["debits"] == "1,071,149.46"


def test_the_box_balances_against_itself():
    _, summary = read_sectioned(statement_pages())
    # 444,383.67 + 1,113,284.17 − 1,071,149.46 = 486,518.38
    assert summary["balanceCheck"] == "ok"
    assert summary["box"]["balances"] is True


def test_the_lists_add_up_to_the_box():
    _, summary = read_sectioned(statement_pages())
    assert summary["debits"] == "9,167.18"  # checks 3,548.72 + debits 5,618.46
    assert summary["credits"] == "9,167.18"  # credits


def test_a_statement_whose_box_does_not_balance_is_said_so():
    pages = statement_pages()
    # Break the ending balance in the box on page 1.
    pages[0].words = [word for word in pages[0].words if word.text != "$486,518.38"]
    pages[0].words.append(r("$999,999.99", 1500, 560))
    _, summary = read_sectioned(pages)
    assert summary["balanceCheck"] == "broken"
    assert summary["box"]["balances"] is False


def test_the_bank_name_is_read_from_the_letterhead():
    _, summary = read_sectioned(statement_pages())
    assert summary["bank"] == "Renasant Bank"


def test_the_head_fields_are_read():
    _, summary = read_sectioned(statement_pages())
    fields = {detail["label"]: detail["value"] for detail in summary["details"]}
    assert fields["Account Number"] == "0019002021"
    assert fields["This Statement"] == "MAY 31, 2026"


# --------------------------------------------------------------------------- #
# The export                                                                   #
# --------------------------------------------------------------------------- #


def test_the_export_gathers_each_section_whole():
    readings, _ = read_sectioned(statement_pages())
    pages = [ExportPage.of(r.number, r.result) for r in readings]
    tables = {t.title: len(t.rows) for t in extra_tables(pages)}
    assert tables == {"Checks": 4, "Other Debits": 2, "Credits": 2}


def test_the_document_export_is_the_three_sections():
    readings, _ = read_sectioned(statement_pages())
    pages = [ExportPage.of(r.number, r.result) for r in readings]
    document = to_document_json(pages, total=len(pages))
    assert document["kind"] == "sections"
    assert [t["title"] for t in document["tables"]] == ["Checks", "Other Debits", "Credits"]


def test_the_csv_writes_each_section_under_its_title():
    readings, _ = read_sectioned(statement_pages())
    pages = [ExportPage.of(r.number, r.result) for r in readings]
    blocks = to_csv(pages, total=len(pages)).split("\r\n\r\n")
    titles = [block.splitlines()[0] for block in blocks]
    assert titles == ["Checks", "Other Debits", "Credits"]
