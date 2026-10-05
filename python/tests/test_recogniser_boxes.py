"""
What the recogniser's boxes look like by the time the readers see them.

    .venv/bin/python -m pytest python/tests/test_recogniser_boxes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from read_receipt import DIGIT_GAP, restore_spaces  # noqa: E402
from reader.boxes import WordBox  # noqa: E402
from reader.glyphs import tighten_boxes  # noqa: E402


def page_with_ink(columns: range, size=(40, 200)) -> np.ndarray:
    grey = np.full(size, 255, dtype=np.uint8)
    grey[5:30, columns.start : columns.stop] = 0
    return grey


def test_a_words_box_is_cut_down_to_its_ink():
    # The recogniser boxes 20..120; the word's ink is only 40..79.
    word = WordBox(text="CASH", x=20, y=5, width=100, height=25, confidence=0.9)
    (tight,) = tighten_boxes([word], page_with_ink(range(40, 80)))
    assert (tight.x, tight.width) == (40.0, 40.0)
    assert tight.text == "CASH" and tight.confidence == 0.9


def test_a_gap_between_two_words_is_real_once_each_is_cut_to_its_ink():
    grey = page_with_ink(range(20, 60))
    grey[5:30, 90:130] = 0
    words = [
        WordBox("ONE", 15, 5, 55, 25, 1.0),   # touches the next box...
        WordBox("TWO", 70, 5, 70, 25, 1.0),
    ]
    one, two = tighten_boxes(words, grey)
    assert two.x - (one.x + one.width) >= 30


def test_a_word_with_no_ink_keeps_its_box():
    word = WordBox("?", 20, 5, 40, 25, 0.5)
    (kept,) = tighten_boxes([word], np.full((40, 200), 255, dtype=np.uint8))
    assert (kept.x, kept.width) == (20.0, 40.0)


def spans(n):
    return [(i * 10.0, i * 10.0 + 8) for i in range(n)]


def test_a_narrow_gap_between_digits_is_inside_a_number():
    # `1234 5678` with a gap just over a word space: one number, not two.
    text = "12345678"
    out, _ = restore_spaces(text, spans(8), [39.0], [4, 4], [DIGIT_GAP - 0.1])
    assert out == "12345678"


def test_a_wide_gap_between_digits_is_a_column():
    text = "12345678"
    out, _ = restore_spaces(text, spans(8), [39.0], [4, 4], [DIGIT_GAP + 0.3])
    assert out == "1234 5678"


def test_a_narrow_gap_between_letters_is_still_a_space():
    text = "ABCDEFGH"
    out, _ = restore_spaces(text, spans(8), [39.0], [4, 4], [0.35])
    assert out == "ABCD EFGH"
