"""
Digits a recogniser read as letters, put right by the page's own typeface.

The pages here are drawn, not read: each token is rendered in a real font and
handed over as the word the recogniser *said* it saw, so what is under test is
the part that looks at the ink. A token that is printed `50X` and said `SOX` is
the failure this exists for.

    .venv/bin/python -m pytest python/tests/test_confusables.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reader.boxes import WordBox  # noqa: E402
from reader.confusables import (  # noqa: E402
    discover_pairs,
    doubt,
    exemplars,
    settle_confusables,
    trusted_pairs,
)

FONT = cv2.FONT_HERSHEY_DUPLEX
SCALE, THICK, ROW = 1.0, 2, 56
AIR, SPACE = 0, 24

#: Tokens whose reading is not in doubt: digits, and capitals, drawn as said.
DIGITS = ["5050", "1552", "3555", "5005", "8505", "5150", "2500", "0550", "4400", "1445", "4144", "2454"]
#: None starts or ends in two lookalikes: a word like `LESS` is itself doubtful, and a
#: page does not teach itself from words it is not sure of.
CAPITALS = [
    "CASH", "BUCKS", "MOST", "DISK", "MUSIC", "FAST", "LOST", "VISIT", "MOON", "ROOM", "LOOP",
    "MARCH", "CHART", "FRAME", "PLANK", "BRAND", "GRAPH",
]


def page(tokens: list[tuple[str, str]], width: int = 640) -> tuple[list[WordBox], np.ndarray]:
    """
    Rows of (said, printed): `said` is the word handed over, `printed` is what is drawn.

    Glyphs are set one at a time with a little air between them, as a line
    printer sets type; a space in `printed` is a real gap.
    """
    pixels = np.full((ROW * (len(tokens) + 1), width, 3), 255, dtype=np.uint8)
    words: list[WordBox] = []
    for row, (said, printed) in enumerate(tokens):
        start, baseline = 30, ROW * (row + 1)
        x, tallest = start, 0
        for mark in printed:
            (w, h), _under = cv2.getTextSize("5" if mark == " " else mark, FONT, SCALE, THICK)
            if mark != " ":
                cv2.putText(pixels, mark, (x, baseline), FONT, SCALE, (20, 20, 20), THICK, cv2.LINE_AA)
                tallest = max(tallest, h)
            x += w + (SPACE if mark == " " else AIR)
        words.append(WordBox(text=said, x=start, y=baseline - tallest, width=x - AIR - start, height=tallest))
    return words, pixels


def typeface(extra: list[tuple[str, str]]) -> tuple[list[WordBox], np.ndarray]:
    return page([(t, t) for t in DIGITS + CAPITALS] + extra)


def read(extra: list[tuple[str, str]]) -> list[str]:
    words, pixels = typeface(extra)
    settled = settle_confusables(words, pixels)
    return [w.text for w in settled[len(DIGITS) + len(CAPITALS):]]


# --------------------------------------------------------------------------- #
# Which words are asked about                                                  #
# --------------------------------------------------------------------------- #


LOOKALIKES = frozenset("OSA")


def test_a_lookalike_among_digits_is_doubtful():
    for text in ("S100", "5O0S", "BLUE7S", "X5OHIGH"):
        assert doubt(text, LOOKALIKES), text


def test_two_lookalikes_at_a_words_end_are_doubtful():
    assert doubt("SOX", LOOKALIKES) and doubt("FIERYSS", LOOKALIKES)


def test_lookalikes_among_letters_are_letters():
    """`CROSSWORD` is not a number, and a page of names is not asked about for nothing."""
    for text in ("CROSSWORD", "CASH", "MONEY", "JACKPOT", "$500", "50X", "002", "Powerball"):
        assert not doubt(text, LOOKALIKES), text


def test_what_is_a_lookalike_is_the_pages_to_say():
    """Nothing is a lookalike until the page's own glyphs have said so."""
    assert not doubt("SOX", frozenset())
    assert doubt("T5", frozenset("T"))
    assert not doubt("T5", LOOKALIKES)


# --------------------------------------------------------------------------- #
# What the page's own glyphs say                                               #
# --------------------------------------------------------------------------- #


def test_a_five_said_as_an_s_is_put_right_by_the_pages_own_fives():
    assert read([("SS", "5S")]) == ["5S"]


def test_an_s_that_is_an_s_is_left_alone():
    """The case a second reading of the same crop got wrong: `7S` became `75`."""
    assert read([("7S", "7S"), ("LUCKY7S", "LUCKY7S")]) == ["7S", "LUCKY7S"]


def test_each_glyph_is_decided_by_itself():
    """`5S` has both marks in it; the first is a five and the second is not."""
    assert read([("SS", "5S")]) == ["5S"]
    assert read([("SS", "SS")]) == ["SS"]


def test_a_word_that_is_not_doubtful_is_not_touched():
    assert read([("CA5H", "CA5H")]) == ["CA5H"]


def test_glyphs_that_do_not_pair_up_with_the_reading_are_left_as_read():
    """A word drawn with three glyphs and read with four is not something to guess at."""
    assert read([("SOXX", "50X")]) == ["SOXX"]


def test_the_ink_decides_not_what_the_recogniser_was_sure_of():
    words, pixels = typeface([("SS", "5S")])
    words[-1] = WordBox(text="SS", x=words[-1].x, y=words[-1].y, width=words[-1].width, height=words[-1].height, confidence=0.99)
    assert [w.text for w in settle_confusables(words, pixels)][-1] == "5S"


# --------------------------------------------------------------------------- #
# A page has to have shown it can tell a pair apart                            #
# --------------------------------------------------------------------------- #


def settled_pairs(words, pixels):
    pairs = discover_pairs(pixels, words)
    found = exemplars(pixels, words, pairs, frozenset(letter for _digit, letter in pairs))
    return pairs, trusted_pairs(found, pairs)


def test_a_pair_is_trusted_on_a_page_that_tells_it_apart():
    words, pixels = typeface([])
    assert ("5", "S") in settled_pairs(words, pixels)[1]


def test_a_page_with_no_glyphs_to_compare_with_changes_nothing():
    words, pixels = page([("SS", "5S"), ("SOX", "50X")])
    assert [w.text for w in settle_confusables(words, pixels)] == ["SS", "SOX"]


def test_a_page_with_too_few_of_a_mark_changes_nothing():
    words, pixels = page([(t, t) for t in ("5050", "CASH", "BUCKS")] + [("SS", "5S")])
    assert [w.text for w in settle_confusables(words, pixels)][-1] == "SS"


def test_a_typeface_that_cannot_tell_a_pair_apart_is_left_alone():
    """Draw every zero as a letter O: on this page the two marks are one shape."""
    zeros = [(t, t.replace("0", "O")) for t in DIGITS if "0" in t]
    plain = [(t, t) for t in DIGITS if "0" not in t] + [(t, t) for t in CAPITALS]
    words, pixels = page(zeros + plain + [("SOX", "5OX")])
    assert ("0", "O") not in settled_pairs(words, pixels)[1]
    # So the `O` is not made a zero, though the `S` still becomes a five.
    assert [w.text for w in settle_confusables(words, pixels)][-1] == "5OX"


# --------------------------------------------------------------------------- #
# Spaces the recogniser swallowed                                              #
# --------------------------------------------------------------------------- #


def spaced(said: str, printed: str) -> list[str]:
    words, pixels = typeface([(said, printed)])
    return [w.text for w in settle_confusables(words, pixels)][len(DIGITS) + len(CAPITALS):]


def test_a_gap_the_text_does_not_explain_is_a_space():
    words, pixels = typeface([("FIERYSS", "FIERY 5S")])
    out = settle_confusables(words, pixels)[len(DIGITS) + len(CAPITALS):]
    assert [w.text for w in out] == ["FIERY", "5S"]
    # Each part sits over the glyphs it is made of, inside the word's own span.
    word = words[-1]
    assert out[0].x == word.x and abs(out[-1].right - word.right) < 1e-6
    assert out[0].right <= out[1].x + 1e-6


def test_a_gap_that_holds_a_printed_point_is_not_a_space():
    assert spaced("S5.00", "55.00") == ["55.00"]


def test_a_word_with_no_wide_gap_stays_one_word():
    assert spaced("SS", "5S") == ["5S"]


def test_the_spaces_in_a_word_are_not_doubled():
    assert spaced("FIERY SS", "FIERY 5S") in (["FIERY SS"], ["FIERY 5S"], ["FIERY", "5S"])


# --------------------------------------------------------------------------- #
# Where there are no pixels                                                    #
# --------------------------------------------------------------------------- #


def test_a_pdf_or_a_page_with_no_pixels_is_left_alone():
    words = [WordBox(text="SOX", x=0, y=0, width=30, height=20)]
    assert [w.text for w in settle_confusables(words, None)] == ["SOX"]
    assert settle_confusables([], None) == []


# --------------------------------------------------------------------------- #
# Nothing is keyed to a word, or to a list of which letter is which digit       #
# --------------------------------------------------------------------------- #


def test_the_pairs_are_the_ones_this_pages_glyphs_show():
    words, pixels = typeface([])
    assert discover_pairs(pixels, words) == [("0", "O"), ("4", "A"), ("5", "S")]


def test_a_pair_no_table_could_have_listed_is_settled_all_the_same():
    """`4` and `A` are alike in this typeface, so a `4` said as an `A` is put right."""
    assert read([("A50", "450"), ("A0A", "404")]) == ["450", "404"]


def test_the_same_page_in_another_typeface_gives_its_own_pairs():
    global FONT
    kept, FONT = FONT, cv2.FONT_HERSHEY_SIMPLEX
    try:
        words, pixels = typeface([])
        pairs = discover_pairs(pixels, words)
        assert ("5", "S") in pairs
        assert [w.text for w in settle_confusables(*typeface([("SS", "5S")]))][-1] == "5S"
    finally:
        FONT = kept


def test_words_nobody_listed_are_read_the_same_as_any_other():
    """Invented vocabulary, drawn and said at random: the fix cannot depend on a word."""
    import random

    rng = random.Random(7)
    letters = "BCDFHJKLMNPQRTUVWXYZ"  # none of which is a lookalike on this page
    tokens, expected = [], []
    for _ in range(12):
        stem = "".join(rng.choice(letters) for _ in range(rng.randint(2, 4)))
        digits = "".join(rng.choice("1234") for _ in range(rng.randint(1, 3)))
        # A five printed as S beside digits, and a plain word that must be left alone.
        tokens.append(("S" + digits, "5" + digits))
        expected.append("5" + digits)
        tokens.append((stem, stem))
        expected.append(stem)
    assert read(tokens) == expected


def test_a_space_is_found_in_a_word_that_is_not_doubtful():
    """Spaces have nothing to do with confusable marks: any word the ink can speak for."""
    words, pixels = typeface([("NEWYORK", "NEW YORK")])
    out = settle_confusables(words, pixels)[len(DIGITS) + len(CAPITALS):]
    assert [w.text for w in out] == ["NEW", "YORK"]


def test_a_narrow_glyph_leaves_air_that_is_not_a_space():
    """A `1` is mostly air: the gap beside it says nothing, in this font or any other."""
    assert read([("1552", "1552"), ("1445", "1445"), ("4144", "4144")]) == ["1552", "1445", "4144"]
