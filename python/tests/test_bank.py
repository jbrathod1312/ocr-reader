"""
The bank statement reader.

It reads no word for its meaning, so these tests change the words: titles in
another language, no titles at all, labels no reader has heard of. The rows, the
columns and the checks must come out the same.

    .venv/bin/python -m pytest python/tests/test_bank.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.bank.checks import audit, infer_roles, txn_from  # noqa: E402
from reader.bank.columns import is_date_token  # noqa: E402
from reader.bank.money import is_money, parse_money  # noqa: E402
from reader.bank.statement import _Page, read_pages  # noqa: E402
from reader.boxes import WordBox  # noqa: E402

D = Decimal
H = 36

# --------------------------------------------------------------------------- #
# Shapes                                                                       #
# --------------------------------------------------------------------------- #


def test_amounts_are_recognised_by_shape():
    assert parse_money("$1,234.56") == D("1234.56")
    assert parse_money("($971,328.54)") == D("-971328.54")
    assert parse_money("1,234.56-") == D("-1234.56")
    assert parse_money("-12.00") == D("-12.00")
    # A mark after the figure is kept in the cell and not interpreted.
    assert parse_money("24.00C") == D("24.00")
    assert parse_money("250.00 DR") is None


def test_a_reference_is_not_an_amount():
    for text in ("1006", "09/16/2026", "RDN", "12/18CT", "20260930E3QP"):
        assert not is_money(text), text


def test_dates_are_recognised_by_shape():
    for text in ("09/15/2026", "9-5-26", "2026-09-15", "09/15//2026", "O9/15/2026"):
        assert is_date_token(text), text
    for text in ("RDN", "12/6CT", "1234"):
        assert not is_date_token(text), text


# --------------------------------------------------------------------------- #
# A page                                                                       #
# --------------------------------------------------------------------------- #


def w(text: str, x: float, y: float) -> WordBox:
    return WordBox(text=text, x=x, y=y, width=len(text) * 13, height=H)


def r(text: str, edge: float, y: float) -> WordBox:
    return w(text, edge - len(text) * 13, y)


# Newest first. Each balance is the one below it plus this row's credit or minus its debit.
ROWS = [
    ("09/16/2026", "1006", "Alpha transfer", "", "100.00", "1,065.00"),
    ("09/15/2026", "", "Bravo purchase", "50.00", "", "965.00"),
    ("09/15/2026", "1004", "Charlie refund", "", "25.00", "1,015.00"),
    ("09/14/2026", "", "Delta fee", "10.00", "", "990.00"),
    ("09/13/2026", "1003", "Echo opening", "5.00", "", "1,000.00"),
]


def page(titles: tuple[str, ...] | None, rows=ROWS, extras: bool = True) -> list[WordBox]:
    words: list[WordBox] = []
    if titles:
        for text, x in zip(titles, (160, 369, 560, 1582, 1767, 1941)):
            words.append(w(text, x, 96))
    y = 150
    for date, ref, text, debit, credit, balance in rows:
        words.append(w(date, 160, y))
        if ref:
            words.append(w(ref, 369, y))
        words.append(w(text, 560, y))
        if debit:
            words.append(r(debit, 1650, y))
        if credit:
            words.append(r(credit, 1835, y))
        words.append(r(balance, 2040, y))
        y += 90
    if extras:
        # A line of figures under the rows with nothing in the balance column,
        # a line of words, and a line out in the margin.
        words += [r("(65.00)", 1650, y + 40), r("125.00", 1835, y + 40)]
        words += [w("anything at all here", 160, y + 130)]
        words += [w("https://example.test/p", 40, y + 220)]
    return words


def read(words: list[WordBox]):
    return read_pages([_Page(1, 2200, 1700, "pdf text", words)])


TITLES = ("Date", "Check", "Description", "Debit", "Credit", "Balance")
OTHER_WORDS = ("Fecha", "Número", "Concepto", "Cargo", "Abono", "Saldo")
GIBBERISH = ("Zorb", "Quill", "Fnord", "Mim", "Plink", "Glorp")


def test_the_titles_are_copied_as_printed_whatever_they_say():
    for titles in (TITLES, OTHER_WORDS, GIBBERISH):
        readings, summary = read(page(titles))
        assert readings[0].result.headers == list(titles)
        assert summary["transactions"] == 5


def test_a_page_with_no_titles_at_all_is_read_the_same():
    readings, summary = read(page(None))
    cells = [row.cells for row in readings[0].result.table_rows]
    assert [c[0] for c in cells] == [row[0] for row in ROWS]
    assert readings[0].result.headers == [f"Column {i + 1}" for i in range(len(cells[0]))]
    assert summary["balanceCheck"] == "ok"


def test_amounts_land_in_the_column_they_are_aligned_with():
    readings, _ = read(page(GIBBERISH))
    cells = [row.cells for row in readings[0].result.table_rows]
    assert cells[0][3:] == ["", "100.00", "1,065.00"]
    assert cells[1][3:] == ["50.00", "", "965.00"]


def test_which_column_is_the_balance_is_found_by_arithmetic_not_by_title():
    for titles in (TITLES, OTHER_WORDS, GIBBERISH):
        _, summary = read(page(titles))
        assert summary["balanceCheck"] == "ok"
        assert summary["closingBalance"] == "1,065.00"
        assert summary["openingBalance"] == "1,005.00"
        assert summary["credits"] == "125.00"
        assert summary["debits"] == "65.00"


def test_figures_under_the_rows_are_the_printed_totals_by_position_alone():
    readings, summary = read(page(GIBBERISH))
    assert summary["stated"]["debits"] == "65.00"
    assert summary["stated"]["credits"] == "125.00"
    reasons = sorted(entry.reason for entry in readings[0].result.skipped)
    assert reasons == ["furniture", "margin", "summary"]


def test_totals_that_do_not_match_the_rows_are_said_so():
    words = page(GIBBERISH)
    words = [x for x in words if x.text != "125.00"] + [r("999.00", 1835, 150 + 5 * 90 + 40)]
    readings, _ = read(words)
    messages = [issue.message for issue in readings[0].result.validation]
    assert any("999.00" in m and "Plink" in m for m in messages)


def test_a_row_with_no_readable_date_is_still_a_row():
    rows = [("", *row[1:]) if row[0] == "09/15/2026" and row[2] == "Charlie refund" else row for row in ROWS]
    readings, summary = read(page(GIBBERISH, rows))
    assert summary["transactions"] == 5
    assert summary["balanceCheck"] == "ok"


# --------------------------------------------------------------------------- #
# What the statement says about itself                                         #
# --------------------------------------------------------------------------- #


def money(*values: str | None) -> list[Decimal | None]:
    return [D(v) if v is not None else None for v in values]


# Columns: out, in, balance. Newest first.
NEWEST_FIRST = [
    money(None, "100.00", "1065.00"),
    money("50.00", None, "965.00"),
    money(None, "25.00", "1015.00"),
    money("10.00", None, "990.00"),
    money("5.00", None, "1000.00"),
]


def test_the_balance_column_and_each_columns_direction_are_found_from_the_numbers():
    roles = infer_roles(NEWEST_FIRST)
    assert roles is not None
    assert roles.balance == 2
    assert roles.descending is True
    assert roles.signs == {0: -1, 1: 1}


def test_an_oldest_first_statement_is_read_the_other_way_round():
    roles = infer_roles(list(reversed(NEWEST_FIRST)))
    assert roles is not None and roles.descending is False and roles.balance == 2


def test_columns_in_another_order_give_the_same_answer():
    shuffled = [[row[2], row[1], row[0]] for row in NEWEST_FIRST]
    roles = infer_roles(shuffled)
    assert roles is not None
    assert roles.balance == 0
    assert roles.signs == {1: 1, 2: -1}


def test_one_signed_column_and_a_balance_is_enough():
    rows = [money("100.00", "1065.00"), money("-50.00", "965.00"), money("25.00", "1015.00"), money("-10.00", "990.00")]
    roles = infer_roles(rows)
    assert roles is not None and roles.balance == 1 and roles.signs == {0: 1}


def test_figures_that_do_not_follow_each_other_are_not_a_ledger():
    rows = [money("17.31", "704.20"), money("2.95", "91.07"), money("33.10", "4.66"), money("48.02", "813.50")]
    assert infer_roles(rows) is None


def audited(values):
    roles = infer_roles(NEWEST_FIRST)
    txns = [txn_from(1, i, row, roles) for i, row in enumerate(values)]
    return audit(txns, roles, {}, ["Out", "In", "Bal"], {}, 1)


def test_a_statement_that_adds_up_is_ok():
    report = audited(NEWEST_FIRST)
    assert report.balance == "ok" and report.issues == {}
    assert report.closing == D("1065.00") and report.opening == D("1005.00")


def test_a_misread_amount_points_at_its_row():
    rows = [list(row) for row in NEWEST_FIRST]
    rows[1][0] = D("60.00")  # 50.00 read as 60.00
    report = audited(rows)
    assert report.balance == "broken"
    assert [i.rows for i in report.issues[1]] == [[1]]


def test_a_misread_balance_points_at_the_row_that_fits_neither_neighbour():
    rows = [list(row) for row in NEWEST_FIRST]
    rows[2][2] = D("1025.00")  # 1015.00 read as 1025.00
    report = audited(rows)
    assert [i.rows for i in report.issues[1]] == [[2]]
    assert "either side" in report.issues[1][0].message
