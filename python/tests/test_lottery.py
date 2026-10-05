"""
The lottery reader, which assumes nothing about the page it is given.

It is not told how many columns a table has, what they are called, or what kind
of page it is; the rows and columns come from where the figures and text sit.
These tests change all of that and expect the same reading.

    .venv/bin/python -m pytest python/tests/test_lottery.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.assemble import assemble_receipt, row_cells  # noqa: E402
from reader.boxes import WordBox  # noqa: E402
from reader.lottery.assemble import assemble_lottery  # noqa: E402
from reader.lottery.table import read_table, repair_token  # noqa: E402
from reader.validate import (  # noqa: E402
    find_totals_row,
    validate_count,
    validate_pairs,
    validate_totals,
)

H = 30
CHAR = 14


def w(text: str, x: float, y: float) -> WordBox:
    return WordBox(text=text, x=x, y=y, width=len(text) * CHAR, height=H)


def grid(rows, xs, y0=300, pitch=45, titles=None, heading=None, extras=()):
    """A page: optional heading and titles, then `rows` set at the column starts `xs`."""
    words: list[WordBox] = []
    if heading:
        words.append(w(heading, 100, 100))
    if titles:
        words += [w(text, x, y0 - pitch) for text, x in zip(titles, xs) if text]
    for n, row in enumerate(rows):
        words += [w(cell, x, y0 + n * pitch) for cell, x in zip(row, xs) if cell]
    words += [w(text, x, y) for text, x, y in extras]
    return words


XS_THREE = (60, 300, 700)
XS_SIX = (88, 176, 564, 625, 684, 750)
XS_EIGHT = (80, 160, 480, 540, 600, 660, 720, 780)

SETTLEMENTS = [
    ("862-021236", "$1,000 MAYHEM", "02/23/26"),
    ("870-011968", "X THE CASH", "02/28/26"),
    ("881-023223", "DIAMONDS & GOLD", "02/28/26"),
    ("868-041163", "INSTANT MILLION", "02/28/26"),
    ("879-009911", "MEGA CASH CROSSWORD", "02/28/26"),
    ("875-010840", "$500 STACKED", "02/28/26"),
]


def counts_table(n_counts: int):
    xs = (88, 176) + tuple(564 + 60 * i for i in range(n_counts))
    rows = [
        (f"8{i}{i}", f"GAME {chr(65 + i)}", *[f"{(i + j) % 7:03d}" for j in range(n_counts)])
        for i in range(1, 8)
    ]
    return xs, rows


# --------------------------------------------------------------------------- #
# Nothing about the table is assumed                                           #
# --------------------------------------------------------------------------- #


def test_three_columns_are_found_without_being_told_there_are_three():
    result = assemble_lottery(grid(SETTLEMENTS, XS_THREE))
    cells = row_cells(result)
    assert len(cells) == 6 and all(len(row) == 3 for row in cells)
    assert cells[1] == ["870-011968", "X THE CASH", "02/28/26"]


def test_the_number_of_count_columns_is_whatever_the_page_has():
    for n in (2, 4, 6):
        xs, rows = counts_table(n)
        cells = row_cells(assemble_lottery(grid(rows, xs)))
        assert all(len(row) == 2 + n for row in cells), n
        assert cells[0][2:] == list(rows[0][2:]), n


def test_the_page_is_not_classed_as_any_kind_of_document():
    assert assemble_lottery(grid(SETTLEMENTS, XS_THREE)).kind == "table"


def test_the_general_reader_still_reads_only_tables_it_finds():
    result = assemble_receipt([w("Just", 60, 100), w("some", 160, 100), w("prose", 260, 100), w("here", 380, 100)])
    assert result.kind == "table" and row_cells(result) == [] and result.headers == []


# --------------------------------------------------------------------------- #
# Titles are whatever the page prints, or nothing                              #
# --------------------------------------------------------------------------- #


def test_titles_over_the_columns_are_copied_as_printed_in_any_language():
    for titles in (("Game-Pack", "Name", "Date Settled"), ("Kod", "Nazwa", "Data"), ("Σ", "Όνομα", "Ημερομηνία")):
        result = assemble_lottery(grid(SETTLEMENTS, XS_THREE, titles=titles))
        assert result.headers == list(titles), titles


def test_a_page_with_no_titles_has_none_and_none_are_made_up():
    result = assemble_lottery(grid(SETTLEMENTS, XS_THREE))
    assert result.headers == ["", "", ""]


def test_the_heading_over_the_table_is_the_title():
    result = assemble_lottery(grid(SETTLEMENTS, XS_THREE, titles=("A", "B", "C"), heading="Pierwszy raport"))
    assert result.title == "Pierwszy raport"


# --------------------------------------------------------------------------- #
# The checks are arithmetic                                                    #
# --------------------------------------------------------------------------- #


def inventory_with_totals(label, totals):
    xs, rows = counts_table(4)
    sums = [sum(int(r[2 + j]) for r in rows) for j in range(4)]
    last = ("", label, *[f"{(t if t is not None else s):03d}" for t, s in zip(totals, sums)])
    return xs, [*rows, last]


def test_a_totals_row_is_found_by_its_sums_whatever_it_is_called():
    for label in ("TOTALS", "Σύνολο", "Zorgle", ""):
        xs, rows = inventory_with_totals(label, (None, None, None, None))
        result = assemble_lottery(grid(rows, xs))
        assert result.validation == [], label
        found = find_totals_row([list(r) for r in row_cells(result)])
        assert found is not None and found[0] == len(rows) - 1, label


def test_a_totals_row_that_does_not_add_up_is_flagged_in_the_column_it_is_off():
    xs, rows = inventory_with_totals("Zorgle", (None, 99, None, None))
    result = assemble_lottery(grid(rows, xs))
    assert [i.code for i in result.validation] == ["table-totals"]
    assert "099" not in result.validation[0].message and "99" in result.validation[0].message


def test_a_figure_that_is_not_a_total_is_not_taken_for_one():
    assert find_totals_row([["a", "1", "2"], ["b", "3", "4"], ["c", "5", "6"], ["d", "7", "8"]]) is None


def test_a_count_under_the_rows_is_the_count_only_when_it_could_be_one():
    assert [i.code for i in validate_count(9, 10)] == ["table-count"]
    assert validate_count(10, 10) == []
    assert validate_count(10, 1) == []  # a page number


HEADER_BLOCK = [("Alpha one", "0.00"), ("Bravo two", "1381.74"), ("Charlie three", "2782.43"),
                ("Delta four", "0.00"), ("Echo five", "5.00"), ("Foxtrot six", "0.00")]


def test_a_total_that_adds_up_is_not_flagged_whatever_it_is_called():
    assert validate_pairs([*HEADER_BLOCK, ("Quux", "4169.17")]) == []


def test_a_total_one_digit_off_its_lines_is_flagged():
    assert [i.code for i in validate_pairs([*HEADER_BLOCK, ("Quux", "4189.17")])] == ["table-sum"]


def test_a_figure_printed_twice_with_one_digit_changed_is_flagged():
    pairs = [*HEADER_BLOCK, ("Quux", "4169.17"), ("Bravo  two", "1,381.74"), ("Charlie three", "2,782.48")]
    assert [i.code for i in validate_pairs(pairs)] == ["table-repeated"]


def test_the_same_label_with_a_different_figure_is_a_different_figure():
    pairs = [("Promo", "16.00"), ("Sales Comm", "93.93"), ("Promo", "0.00"), ("Sales Comm", "495.00")]
    assert validate_pairs(pairs) == []


# --------------------------------------------------------------------------- #
# What a recogniser does to a page                                             #
# --------------------------------------------------------------------------- #


def test_counts_the_recogniser_ran_together_are_cut_into_their_columns():
    xs, rows = counts_table(4)
    words = grid(rows, xs)
    # Replace one row's four counts with a single token spanning them.
    y = 300 + 2 * 45
    words = [x for x in words if not (x.y == y and x.x >= 564)]
    words.append(WordBox(text="".join(rows[2][2:]), x=564, y=y, width=4 * 50, height=H))
    cells = row_cells(assemble_lottery(words))
    assert cells[2][2:] == list(rows[2][2:])


def test_marks_in_the_margin_beside_the_table_are_not_cells():
    xs, rows = counts_table(4)
    words = grid(rows, xs) + [w("S", 20, 300 + 45), w("TE", 18, 300 + 3 * 45)]
    cells = row_cells(assemble_lottery(words))
    assert [row[0] for row in cells] == [r[0] for r in rows]


def test_a_misread_figure_is_put_right_by_its_shape():
    assert repair_token("1.381.74") == "1,381.74"
    assert repair_token("$50.000") == "$50,000"
    assert repair_token("$1.00O.000") == "$1,000,000"
    assert repair_token("40.00C") == "40.00C"
    assert repair_token("5.00") == "5.00"


def test_a_row_further_left_than_the_ones_above_keeps_its_figures_in_order():
    # Photographs drift: the last row of counts sits left of its column.
    xs, rows = counts_table(4)
    words = grid(rows, xs)
    last_y = 300 + 6 * 45
    words = [WordBox(x.text, x.x - 34, x.y, x.width, x.height, x.confidence) if x.y == last_y and x.x >= 564 else x for x in words]
    cells = row_cells(assemble_lottery(words))
    assert cells[6][2:] == list(rows[6][2:])
