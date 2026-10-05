"""
The titles a page prints over its columns, copied as printed.

Nothing here knows what a title says. The titles are the nearest line above the
first row that sits over several of the columns and holds neither dates nor
figures; each column's title is whatever words are over it.
"""

from __future__ import annotations

from typing import Sequence

from ..boxes import WordBox, group_into_lines, line_band
from .columns import Column, clean, is_date_token, usable
from .money import is_money


def read_labels(
    words: Sequence[WordBox],
    columns: Sequence[Column],
    first_top: float,
    height: float,
) -> tuple[list[str], list[WordBox]] | None:
    """The titles over `columns` and the words they are made of, or None."""
    if len(columns) < 2:
        return None
    above = [w for w in usable(words) if w.bottom <= first_top + 1]
    lines = sorted(group_into_lines(above, 0.5), key=lambda line: -line_band(line).bottom)
    for line in lines:
        band = line_band(line)
        if first_top - band.bottom > height * 3:
            break
        if any(is_money(w.text) or is_date_token(w.text) for w in line):
            continue
        owner = {id(w): min(range(len(columns)), key=lambda i: columns[i].distance(w)) for w in line}
        over = {index for index in owner.values() if any(columns[index].distance(w) == 0 for w in line if owner[id(w)] == index)}
        if len(over) < 2 or len(over) < len(columns) * 0.5:
            continue
        labels = [
            clean(" ".join(w.text for w in sorted(line, key=lambda w: w.x) if owner[id(w)] == index))
            for index in range(len(columns))
        ]
        return labels, list(line)
    return None
