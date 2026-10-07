"""
The fields a page prints beside its table, read by punctuation and position.

A statement's head is label and value — `Account Number: 107210006444`, or
`Available Balance:` with the figure printed under it — and which is which is a
matter of shape. A colon ends a label; the value is what follows it: the rest of
that run, the run sitting under it, or the next run along. No word is read for
its meaning here either, so a head whose labels are in another language, or
whose labels are ones no reader has heard of, comes out the same.

What this cannot do is say what a field *means*. It reports `Collected Balance`
as the page prints it and makes nothing of it, which is the point: a reader that
knew what a collected balance was would also have to be told, bank by bank, what
each of them calls one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..boxes import WordBox, group_into_lines, line_band

#: A run is a label when one of its words ends in a colon. The colon has to end
#: the word: `2:11 PM` and `https://…` carry one inside a word and are not
#: labels, which is a matter of where the mark sits and not of what it says.
LABEL_END = ":"

#: A label is a short run of words. Longer than this and the colon is punctuation
#: in a sentence — a notice, a footnote — rather than the head of a field.
LABEL_WORDS = 5

#: Runs are split where the space between two words grows past this much of a
#: line's height. A page sets its fields apart by more space than it leaves
#: between the words of one.
RUN_GAP = 0.9

#: How far under a label its value may sit, in line heights, and how far along.
VALUE_BELOW = 2.0
VALUE_BESIDE = 2.5


@dataclass(slots=True)
class Field:
    """One label and the value printed with it, both as printed."""

    label: str
    value: str
    page: int
    #: Top edge of the label, so the fields read in printed order.
    y: float


@dataclass(slots=True)
class _Run:
    words: list[WordBox]
    left: float
    right: float
    top: float
    #: The value this run has been read as, so no run is read as two.
    taken: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words).strip()

    def overlaps(self, other: "_Run") -> float:
        """How much of the narrower run sits under or over the other, 0 to 1."""
        span = min(self.right, other.right) - max(self.left, other.left)
        width = min(self.right - self.left, other.right - other.left)
        return span / width if width > 0 and span > 0 else 0.0


def _runs_of(line: Sequence[WordBox], height: float) -> list[_Run]:
    runs: list[_Run] = []
    for word in sorted(line, key=lambda w: w.x):
        if runs and word.x - runs[-1].right <= height * RUN_GAP:
            runs[-1].words.append(word)
            runs[-1].right = max(runs[-1].right, word.right)
            continue
        runs.append(_Run([word], word.x, word.right, word.y))
    return runs


def _label_of(run: _Run) -> tuple[str, str] | None:
    """The run's label and whatever of its value is printed on the same run."""
    for index, word in enumerate(run.words[:LABEL_WORDS]):
        if not word.text.rstrip().endswith(LABEL_END):
            continue
        label = " ".join(w.text.strip() for w in run.words[: index + 1]).strip()
        label = label[: -len(LABEL_END)].strip()
        if not label or "/" in label or not any(c.isalnum() for c in label):
            return None
        return label, " ".join(w.text.strip() for w in run.words[index + 1 :]).strip()
    return None


def read_fields(words: Sequence[WordBox], height: float, page: int) -> list[Field]:
    """
    The label-and-value fields printed on a page, in printed order.

    `words` is what is left of the page once the table has taken its own: a
    field is by definition not a row.
    """
    lines = sorted(group_into_lines(list(words), 0.5), key=lambda line: line_band(line).top)
    rows = [_runs_of(line, height) for line in lines]
    labelled = [[_label_of(run) for run in runs] for runs in rows]

    fields: list[Field] = []
    for y, (runs, labels) in enumerate(zip(rows, labelled)):
        for x, (run, found) in enumerate(zip(runs, labels)):
            if found is None or run.taken:
                continue
            label, printed = found
            value = printed or _value_under(run, rows, labelled, y, height) or _value_beside(
                runs, labels, x, height
            )
            if value:
                fields.append(Field(label, value, page, run.top))
    return fields


def _value_under(
    run: _Run,
    rows: Sequence[Sequence[_Run]],
    labelled: Sequence[Sequence[tuple[str, str] | None]],
    y: int,
    height: float,
) -> str | None:
    """The run printed under a label, which is where a column of fields puts it."""
    for below, labels in zip(rows[y + 1 :], labelled[y + 1 :]):
        for candidate, found in zip(below, labels):
            if candidate.top - run.top > height * (VALUE_BELOW + 1):
                return None
            if found is not None or candidate.taken or candidate.overlaps(run) < 0.5:
                continue
            candidate.taken = True
            return candidate.text
    return None


def _value_beside(
    runs: Sequence[_Run],
    labels: Sequence[tuple[str, str] | None],
    x: int,
    height: float,
) -> str | None:
    """The next run along, for a label whose value is set beside it on the line."""
    if x + 1 >= len(runs):
        return None
    run, following, found = runs[x], runs[x + 1], labels[x + 1]
    if found is not None or following.taken or following.left - run.right > height * VALUE_BESIDE:
        return None
    following.taken = True
    return following.text
