"""
Dates as a page prints them, and as a recogniser misreads them.

Shapes only: a date is digits and separators, and a letter standing where a
digit is printed (a watermark makes `0` an `o`) is put right where the whole
token is shaped like a date. Nothing here looks a word up.
"""

from __future__ import annotations

import re


def token_core(text: str) -> str:
    return re.sub(r"^[,:;]+|[,:;]+$", "", text.strip())


#: Letters the recogniser puts where a digit is printed, most often under a
#: watermark: 0 as O or Q, 1 as I or l, 2 as Z, 5 as S, 6 as G, 8 as B, 9 as g or q.
_DIGIT_LOOKALIKES = str.maketrans("OoQDIl|iZzSsGBgq", "0000111122556899")
_LOOKALIKE = r"[\dOoQDIl|iZzSsGBgq]"
_DATE_SHAPED = re.compile(rf"^{_LOOKALIKE}{{1,2}}/{_LOOKALIKE}{{1,2}}/{_LOOKALIKE}{{2,4}}$")
#: The same with one slash read as a character: `o2/28726`.
_DATE_ONE_SLASH = re.compile(rf"^{_LOOKALIKE}{{1,2}}/{_LOOKALIKE}{{4,6}}$")


def restore_date_digits(token: str) -> str:
    """
    `o2/28/26` as `02/28/26`, `O5/1S/26` as `05/15/26`.

    A letter is only taken for a digit in text that is shaped like a date,
    mostly digits already, and a possible date once restored: a name or a code
    with slashes in it is not rewritten.
    """
    core = token_core(token)
    marks = core.replace("/", "")
    if sum(char.isdigit() for char in marks) * 2 < len(marks):
        return token
    if _DATE_ONE_SLASH.match(core):
        # `settled_date` finds the lost separator and checks the month and day.
        return core.translate(_DIGIT_LOOKALIKES)
    if not _DATE_SHAPED.match(core):
        return token
    month, day, _year = core.translate(_DIGIT_LOOKALIKES).split("/")
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return token
    return core.translate(_DIGIT_LOOKALIKES)


def settled_date(text: str) -> str | None:
    """A date as `dd/dd/dd`, including the forms a watermark drives the reader into."""
    digits = re.sub(r"\D", "", restore_date_digits(token_core(text)))
    arrangements: list[str] = []
    if len(digits) == 6:
        arrangements.append(digits)
    elif len(digits) == 7:
        # One separator dropped entirely, the other read as a digit.
        arrangements.append(digits[:2] + digits[3:])
        arrangements.append(digits[:4] + digits[5:])
    elif len(digits) == 8:
        arrangements.append(digits[:2] + digits[3:5] + digits[6:])
    for candidate in arrangements:
        month = int(candidate[:2])
        day = int(candidate[2:4])
        if month < 1 or month > 12 or day < 1 or day > 31:
            continue
        return f"{candidate[:2]}/{candidate[2:4]}/{candidate[4:]}"
    return None

