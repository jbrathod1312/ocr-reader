"""
Digits a recogniser read as letters, put right by the page's own typeface.

`5` and `S`, `0` and `O`, `1` and `I` are one stroke apart in a line-printer
face, and a recogniser reading a line gives one answer per glyph with no sign of
how close the call was. The page this was found on reads `50X` as `SOX` at 0.95
confidence and the same printed word three rows down as `50X` at 0.82: its
confidence does not know. Asking the recogniser again does not help either —
cropped and re-read it is right for one word and wrong for the next, and the
frame that fixes `FIERY 5S` turns `LUCKY 7S` into `LUCKY 75`. A second opinion
from a model with the same bias is the same opinion.

What the page does hold is the answer, in its own typeface: dozens of `5`s in
`850` and `005`, dozens of `S`s in `CASH` and `BUCKS`, all printed by the same
machine the doubtful glyph was. So the doubtful glyph is compared with those, by
shape, and nothing else.

Two things keep that honest. A glyph is only changed when its five nearest
neighbours among the page's own glyphs all say the same, so a glyph that looks
like neither is left as read. And a pair of marks is only trusted on a page that
has shown it can tell them apart: each exemplar is classified by the others, and
the pair is used only if the decisions made that way were right nearly every
time. A typeface in which `0` and `O` are the same shape is one where nothing is
changed about them, and the page says so itself.

Nothing here looks a word up or reads what a word means, and there is no table
of which letter resembles which digit either: the pairs are the ones this page's
own glyphs show to be alike (its `5`s are nearer its `S`s than anything else),
so a typeface that confuses other marks is handled the same way. What is fixed is
only the kind of mistake looked for — a digit taken for a letter, at a place where
a figure could be — and the numbers that decide how sure the page has to be.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .boxes import WordBox

#: Longest word whose ends are worth doubting. A long word with two lookalikes
#: at one end is a word; `50X` and `FIERY 5S` are what this is for.
LONGEST_END_DOUBT = 10

#: Every glyph is compared at this size, so a word set in large type and one in
#: small are the same shapes.
GLYPH_W, GLYPH_H = 12, 18

#: Neighbours consulted, all of which must agree for a glyph to be called.
NEIGHBOURS = 5

#: Exemplars a mark needs before its pair is considered, and decisions the pair
#: has to have made (about its own exemplars) before they are believed.
LEAST_EXEMPLARS = 5
LEAST_DECISIONS = 6

#: Share of those decisions that must have been right. A pair that cannot tell
#: itself apart on its own page is not going to tell a stranger.
TRUSTED_PRECISION = 0.97

#: Exemplars kept per mark: plenty to decide on, and the cost of a vote is
#: linear in them.
MOST_EXEMPLARS = 60

#: Readings a recogniser is surest of, which is where exemplars come from.
EXEMPLAR_CONFIDENCE = 0.9

#: A gap in the ink is a space the recogniser swallowed when it is both wide in
#: itself, as a share of the letters' height, and well clear of the gaps between
#: the word's own letters. A gap that holds a printed `.` or `,` is explained by
#: the text and is not one.
SPACE_OF_HEIGHT = 0.35
SPACE_OVER_LETTER_GAPS = 2.2

#: Letter gaps the rest of a word must show before a gap is judged against them.
#: A short word has too few to say what a normal gap is, so there a gap has to be
#: much wider before it is believed: a narrow `l` after a wide `M` is not a space.
LEAST_LETTER_GAPS = 2
SPACE_OF_HEIGHT_IN_SHORT_WORD = 0.55

#: A glyph narrower than this share of the word's typical glyph leaves a hole
#: beside it that is not a space — a `1` or an `i` is mostly air — so a gap next
#: to one is held to the same strict bar as a gap in a short word.
NARROW_GLYPH = 0.6


def doubt(text: str, lookalikes: frozenset[str]) -> bool:
    """
    Whether a reading has marks that could have been read either way.

    `lookalikes` are the letters this page's own glyphs show to resemble a digit.
    One standing among digits is the clearest case: a letter in the middle of a
    figure is not what the page says. Two of them together at either end of a
    word is the other: `SOX` for `50X`, `FIERYSS` for `FIERY 5S`. In the middle
    of a word they are letters among letters, and `CROSSWORD` is not a number.
    """
    marks = text.strip()
    if len(marks) < 2 or not lookalikes:
        return False
    for index, mark in enumerate(marks):
        if mark not in lookalikes:
            continue
        before = marks[index - 1] if index else ""
        after = marks[index + 1] if index + 1 < len(marks) else ""
        if before.isdigit() or after.isdigit():
            return True
    if len(marks) > LONGEST_END_DOUBT:
        return False
    return any(all(mark in lookalikes for mark in end) for end in (marks[:2], marks[-2:]))


def discover_pairs(pixels: np.ndarray, words: Sequence[WordBox]) -> list[tuple[str, str]]:
    """
    The digit and letter pairs this page's own glyphs show to be alike.

    A digit and a letter are a pair when each is the other's nearest across the
    two kinds: the page's average `5` is nearer its average `S` than any other
    letter, and its average `S` is nearer `5` than any other digit. Nothing says
    which marks those are, so a typeface in which `0` is nearest `D`, or `1`
    nearest `I`, gives those pairs instead. A page with too few of either kind
    gives none, and then nothing is changed.

    Taught from every confident token of only digits or only letters, doubtful or
    not: a handful of misread teachers cannot move an average built from dozens.
    """
    digits: dict[str, list[np.ndarray]] = {}
    letters: dict[str, list[np.ndarray]] = {}
    for word in words:
        text = "".join(word.text.split())
        if word.confidence < EXEMPLAR_CONFIDENCE or len(text) < 2:
            continue
        if text.isdigit():
            into = digits
        elif text.isalpha():
            into = letters
        else:
            continue
        paired = _aligned(pixels, word)
        if paired is None:
            continue
        for index, glyph in paired:
            bucket = into.setdefault(word.text[index], [])
            if len(bucket) < MOST_EXEMPLARS:
                bucket.append(glyph.shape)
    mean_digit = {m: np.mean(v, axis=0) for m, v in digits.items() if len(v) >= LEAST_EXEMPLARS}
    mean_letter = {m: np.mean(v, axis=0) for m, v in letters.items() if len(v) >= LEAST_EXEMPLARS}
    if not mean_digit or not mean_letter:
        return []

    def nearest(shape: np.ndarray, among: dict[str, np.ndarray]) -> str:
        return min(among, key=lambda mark: float(np.abs(shape - among[mark]).mean()))

    pairs = []
    for digit, shape in mean_digit.items():
        letter = nearest(shape, mean_letter)
        if nearest(mean_letter[letter], mean_digit) == digit:
            pairs.append((digit, letter))
    return sorted(pairs)


def _printed(text: str) -> list[int]:
    """Where in `text` a glyph is printed: marks with ink, not spaces or small points."""
    return [i for i, mark in enumerate(text) if mark.isalnum() or mark == "$"]


class Glyph:
    """One printed mark: its shape, and the stretch of the page it stands across."""

    __slots__ = ("shape", "left", "right", "height")

    @property
    def width(self) -> float:
        return self.right - self.left

    def __init__(self, shape: np.ndarray, left: float, right: float, height: float):
        self.shape = shape
        self.left = left
        self.right = right
        self.height = height


def _glyphs(pixels: np.ndarray, word: WordBox) -> list[Glyph]:
    """
    The word's glyphs, left to right.

    Read in the brightest of the three colour planes: ink is dark in all of
    them, while paper and a tinted shape printed behind the row are both light
    in at least one, so the shape disappears into the paper whatever colour it
    is, and nothing has to know that there is one.

    Glyphs are told from specks and points by the ink itself, not by the box the
    recogniser drew round the word, which is taller than the letters in it.
    """
    import cv2

    pad = max(1, int(word.height * 0.08))
    top, bottom = max(0, int(word.y) - pad), min(pixels.shape[0], int(word.bottom) + pad)
    left, right = max(0, int(word.x) - pad), min(pixels.shape[1], int(np.ceil(word.right)) + pad)
    if bottom - top < 6 or right - left < 6:
        return []
    crop = pixels[top:bottom, left:right]
    bright = crop[:, :, :3].max(axis=2) if crop.ndim == 3 else crop
    _level, ink = cv2.threshold(bright, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _labels, stats, _centres = cv2.connectedComponentsWithStats(ink, connectivity=8)
    parts = [stats[i] for i in range(1, count) if stats[i][cv2.CC_STAT_WIDTH] >= 2 and stats[i][cv2.CC_STAT_HEIGHT] >= 3]
    if not parts:
        return []
    tallest = max(part[cv2.CC_STAT_HEIGHT] for part in parts)
    parts = [part for part in parts if part[cv2.CC_STAT_HEIGHT] >= 0.55 * tallest]
    parts.sort(key=lambda part: part[cv2.CC_STAT_LEFT])
    glyphs = []
    for x, y, width, height, _area in parts:
        shape = (
            cv2.resize(ink[y : y + height, x : x + width], (GLYPH_W, GLYPH_H), interpolation=cv2.INTER_AREA)
            .astype(np.float32)
            .ravel()
            / 255.0
        )
        glyphs.append(Glyph(shape, left + float(x), left + float(x + width), float(height)))
    return glyphs


def _aligned(pixels: np.ndarray, word: WordBox) -> list[tuple[int, Glyph]] | None:
    """Each printed mark of the word with its glyph, or None where they do not pair up."""
    spots = _printed(word.text)
    glyphs = _glyphs(pixels, word)
    if not spots or len(glyphs) != len(spots):
        # Two glyphs that touch, or one that broke in two: not something to
        # guess at, so the word is left as it was read.
        return None
    return list(zip(spots, glyphs))


def exemplars(
    pixels: np.ndarray,
    words: Sequence[WordBox],
    pairs: Sequence[tuple[str, str]],
    lookalikes: frozenset[str],
) -> dict[str, list[np.ndarray]]:
    """
    The page's own glyphs of each mark in a pair, from the readings it is surest of.

    A digit comes from a token of nothing but digits and a letter from a word of
    nothing but letters, so neither is a glyph that was itself in doubt; and
    never from a word that is doubtful, which would teach `SOX`'s S as a letter.
    """
    found: dict[str, list[np.ndarray]] = {}
    wanted = {mark for pair in pairs for mark in pair}
    for word in words:
        text = "".join(word.text.split())
        if word.confidence < EXEMPLAR_CONFIDENCE or len(text) < 2 or doubt(word.text, lookalikes):
            continue
        if not (text.isdigit() or text.isalpha()):
            continue
        marks = {m for m in text if m in wanted}
        if not marks or all(len(found.get(m, ())) >= MOST_EXEMPLARS for m in marks):
            continue
        paired = _aligned(pixels, word)
        if paired is None:
            continue
        for index, glyph in paired:
            mark = word.text[index]
            if mark in marks and len(found.setdefault(mark, [])) < MOST_EXEMPLARS:
                found[mark].append(glyph.shape)
    return found


def _call(shape: np.ndarray, pool: Sequence[tuple[str, np.ndarray]]) -> str | None:
    """What the nearest glyphs on the page all say the shape is, or None where they differ."""
    if len(pool) < NEIGHBOURS:
        return None
    nearest = sorted(pool, key=lambda item: float(np.abs(shape - item[1]).mean()))[:NEIGHBOURS]
    marks = {mark for mark, _ in nearest}
    return marks.pop() if len(marks) == 1 else None


def trusted_pairs(
    found: dict[str, list[np.ndarray]], pairs: Sequence[tuple[str, str]]
) -> set[tuple[str, str]]:
    """
    The pairs this page has shown it can tell apart.

    Each exemplar is called by all the others and the call is checked. A pair is
    used only if the calls it made this way were nearly all right: a typeface in
    which `0` and `O` are one shape is a page on which they are not told apart,
    and the page is what says so.
    """
    trusted: set[tuple[str, str]] = set()
    for digit, letter in pairs:
        digits, letters = found.get(digit, []), found.get(letter, [])
        if len(digits) < LEAST_EXEMPLARS or len(letters) < LEAST_EXEMPLARS:
            continue
        pool = [(digit, s) for s in digits] + [(letter, s) for s in letters]
        decided = right = 0
        for index, (mark, shape) in enumerate(pool):
            said = _call(shape, pool[:index] + pool[index + 1 :])
            if said is None:
                continue
            decided += 1
            right += said == mark
        if decided >= LEAST_DECISIONS and right / decided >= TRUSTED_PRECISION:
            trusted.add((digit, letter))
    return trusted


def settle_confusables(words: Sequence[WordBox], pixels: np.ndarray | None) -> list[WordBox]:
    """
    `words` with the page's own typeface brought to bear on what the recogniser said.

    Two things, both read from the ink and neither from the words. A capital that
    is a digit in this page's typeface becomes the digit: only in a doubtful
    word, and only when the page's own glyphs say so unanimously and the page
    has shown it can tell the two apart. And a space the recogniser swallowed is
    put back, in any word, where the ink has a gap the text does not explain.
    """
    if pixels is None or len(words) == 0:
        return [word.copy() for word in words]

    # Which letters are digits in disguise, if the page has the glyphs to say.
    pairs = discover_pairs(pixels, words)
    lookalikes = frozenset(letter for _digit, letter in pairs)
    trusted: set[tuple[str, str]] = set()
    pools: dict[tuple[str, str], list[tuple[str, np.ndarray]]] = {}
    if pairs:
        found = exemplars(pixels, words, pairs, lookalikes)
        trusted = trusted_pairs(found, pairs)
        pools = {
            pair: [(pair[0], s) for s in found[pair[0]]] + [(pair[1], s) for s in found[pair[1]]]
            for pair in trusted
        }
    by_letter = {letter: digit for digit, letter in trusted}

    out: list[WordBox] = []
    for word in words:
        paired = _aligned(pixels, word)
        if paired is None:
            out.append(word.copy())
            continue
        text = list(word.text)
        if by_letter and doubt(word.text, lookalikes):
            for spot, glyph in paired:
                digit = by_letter.get(text[spot])
                if digit is not None and _call(glyph.shape, pools[(digit, text[spot])]) == digit:
                    text[spot] = digit
        out.extend(_spaced(word, "".join(text), paired))
    return out


def _spaced(word: WordBox, text: str, paired: Sequence[tuple[int, Glyph]]) -> list[WordBox]:
    """
    `text` laid back over the word's span, split where the ink shows a space.

    A space the recogniser swallowed is a gap in the ink far wider than the
    gaps between the word's own letters. It is only believed where the text
    has nothing there already: a gap holding a `.` is the point, and a gap the
    text already spaces is spaced.
    """
    cuts: list[tuple[int, float]] = []
    gaps = [(a, b, b[1].left - a[1].right) for a, b in zip(paired, paired[1:])]
    for a, b, gap in gaps:
        between = text[a[0] + 1 : b[0]]
        if between:
            continue  # a mark or a space is printed here already
        others = [g for _a, _b, g in gaps if g is not gap and not text[_a[0] + 1 : _b[0]]]
        tall = max(a[1].height, b[1].height)
        # A narrow glyph is mostly air beside it, which says nothing about spaces.
        typical = float(np.median([g.width for _i, g in paired])) if len(paired) >= 3 else 0.0
        narrow = typical > 0 and min(a[1].width, b[1].width) < NARROW_GLYPH * typical
        if len(others) < LEAST_LETTER_GAPS or narrow:
            believed = gap >= SPACE_OF_HEIGHT_IN_SHORT_WORD * tall
        else:
            letter_gap = float(np.median(others))
            believed = gap >= SPACE_OF_HEIGHT * tall and gap >= SPACE_OVER_LETTER_GAPS * max(letter_gap, 1.0)
        if believed:
            cuts.append((a[0] + 1, (a[1].right + b[1].left) / 2))
    if not cuts:
        return [_with_text(word, text)]
    pieces: list[WordBox] = []
    start_text, start_x = 0, word.x
    for at, edge in cuts + [(len(text), word.right)]:
        pieces.append(
            WordBox(
                text=text[start_text:at],
                x=start_x,
                y=word.y,
                width=max(1.0, edge - start_x),
                height=word.height,
                confidence=word.confidence,
            )
        )
        start_text, start_x = at, edge
    return pieces


def _with_text(word: WordBox, text: str) -> WordBox:
    return WordBox(
        text=text, x=word.x, y=word.y, width=word.width, height=word.height, confidence=word.confidence
    )
