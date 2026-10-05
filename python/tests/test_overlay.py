"""
Lettering of a red watermark that the recogniser read as part of a line.

    .venv/bin/python -m pytest python/tests/test_overlay.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from read_receipt import is_overlay_line, trim_overlay_edges  # noqa: E402

CHAR = 10  # pixels per character
HEIGHT = 20


#: Watermarks of different colours: print is dark in every channel, these are not.
PINK = (210, 120, 120)
BLUE = (110, 120, 215)
GREEN = (115, 200, 125)
YELLOW = (225, 215, 90)


def page(
    text: str, overlay: str, tint: tuple[int, int, int] = PINK
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """A strip of paper: `overlay[i]` says whether character i is watermark or black print."""
    strip = np.full((HEIGHT, CHAR * len(text), 3), 235, dtype=np.uint8)
    spans = []
    for index, kind in enumerate(overlay):
        left = index * CHAR
        # Glyph boxes leave a little paper between them.
        spans.append((float(left), float(left + CHAR - 3)))
        if kind == "r":  # bright red, nothing printed
            strip[:, left : left + CHAR - 3] = tint
        elif kind == "k":  # black print, however it was tinted
            strip[:, left : left + CHAR - 3] = (55, 50, 50)
    return strip, spans


def trim(text: str, overlay: str, tint: tuple[int, int, int] = PINK) -> str:
    strip, spans = page(text, overlay, tint)
    return trim_overlay_edges(text, spans, strip, 0.0, float(HEIGHT), 1.0)[0]


def test_watermark_letters_in_front_of_a_line_are_cut():
    assert trim("AdkRAFFLE Cashes", "rrrkkkkkkkkkkkkk") == "RAFFLE Cashes"


def test_a_trailing_letter_of_the_overlay_is_cut():
    assert trim("Cashesk", "kkkkkkr") == "Cashes"


def test_a_letter_in_the_middle_is_left_alone():
    # Print on both sides of it: whatever it is, it is inside the line.
    assert trim("Cash x Cashes", "kkkkkrkkkkkkk") == "Cash x Cashes"


def test_a_line_with_no_print_in_it_is_not_thrown_away():
    # Pale ink, or a stamp read as text: nothing here says which, so keep it.
    assert trim("Arkansas", "rrrrrrrr") == "Arkansas"


def test_a_line_of_print_is_untouched():
    assert trim("TOTAL DUE", "kkkkkkkkk") == "TOTAL DUE"


def test_the_colour_of_the_watermark_does_not_matter():
    for tint in (PINK, BLUE, GREEN, YELLOW):
        assert trim("AdkRAFFLE Cashes", "rrrkkkkkkkkkkkkk", tint) == "RAFFLE Cashes", tint


def test_the_lettering_does_not_matter_either():
    assert trim("Scholarship TOTAL DUE", "rrrrrrrrrrrkkkkkkkkkk", BLUE) == "TOTAL DUE"


def test_a_line_made_only_of_overlay_is_recognised_as_one():
    strip, spans = page("Arkansas", "rrrrrrrr", GREEN)
    assert is_overlay_line("Arkansas", spans, strip, 0.0, float(HEIGHT), 1.0)


def test_a_line_with_print_in_it_is_not_overlay():
    strip, spans = page("Arkansas", "rrrkkrrr", GREEN)
    assert not is_overlay_line("Arkansas", spans, strip, 0.0, float(HEIGHT), 1.0)


def test_a_figure_is_never_taken_for_overlay():
    strip, spans = page("4.169.17", "rrrrrrrr", PINK)
    assert not is_overlay_line("4.169.17", spans, strip, 0.0, float(HEIGHT), 1.0)
