"""Amounts, recognised by their shape and never by a word beside them."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: `$1,234.56`, `(1,234.56)`, `-1,234.56`, `1,234.56-`. Two decimals always: a
#: statement prints cents, and a bare `1234` is a reference. A mark of a letter
#: or two after the figure (`24.00C`) is kept in the cell and ignored here; what
#: it means is for the arithmetic to find out, not for this to know.
MONEY = re.compile(r"^\(?-?[$€£]?\s?-?[\d,]*\d\.\d{2}\)?-?[A-Za-z]{0,2}$")


def is_money(text: str) -> bool:
    return bool(MONEY.match(text.strip()))


def parse_money(text: str) -> Decimal | None:
    """
    The figure as printed: negative in parentheses or with a minus, else positive.

    Which of a statement's columns adds to the balance and which takes from it
    is not decided here; `checks` works it out from the numbers.
    """
    token = text.strip()
    if not is_money(token):
        return None
    negative = token.startswith("(") or token.startswith("-") or bool(re.match(r"^[$€£]\s?-", token))
    body = re.sub(r"[A-Za-z]+$", "", token)
    negative = negative or body.endswith("-")
    try:
        value = Decimal(re.sub(r"[^\d.]", "", body))
    except InvalidOperation:
        return None
    return -value if negative else value


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}"
