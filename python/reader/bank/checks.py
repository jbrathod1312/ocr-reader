"""
What a statement says about itself, found by arithmetic.

Each row prints the balance after it, so the balance is a running proof: from
one row to the next it changes by exactly what the row's amounts say. That is
also how the columns are told apart — no title is read. The balance column is
the one whose changes equal the other columns' figures, and each of those adds
to the balance or takes from it according to which way its figures move it.

A misread digit breaks the proof, and the break says which row to look at. The
figures printed under the rows, where there are any, are held against the sums.

Nothing here changes what was read. It only says which rows to look at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from ..validate import ValidationIssue
from .money import format_money

EPSILON = Decimal("0.005")
Row = Sequence["Decimal | None"]


@dataclass(slots=True)
class Roles:
    #: Index, among the money columns, of the running balance.
    balance: int
    #: +1 where a column's figures add to the balance, -1 where they take away.
    signs: dict[int, int]
    #: Whether the newest row is the first.
    descending: bool


def _delta(values: Sequence[Row], i: int, balance: int, descending: bool) -> tuple[Decimal, Row] | None:
    """The change in balance between rows i and i+1, and the row whose amounts caused it."""
    a, b = values[i][balance], values[i + 1][balance]
    if a is None or b is None:
        return None
    return (a - b, values[i]) if descending else (b - a, values[i + 1])


def infer_roles(values: Sequence[Row]) -> Roles | None:
    """
    Which money column is the running balance, and which way each other one moves it.

    Tried for every column as the balance, both ways round. The right one
    explains most neighbouring rows; a wrong one explains almost none.
    """
    if len(values) < 3 or not values[0]:
        return None
    width = len(values[0])
    best: tuple[tuple[int, int], Roles] | None = None
    for balance in range(width):
        for descending in (True, False):
            plus = [0] * width
            minus = [0] * width
            pairs = 0
            for i in range(len(values) - 1):
                found = _delta(values, i, balance, descending)
                if found is None:
                    continue
                pairs += 1
                delta, row = found
                for c in range(width):
                    if c == balance or row[c] is None or row[c] == 0:
                        continue
                    if abs(delta - row[c]) < EPSILON:
                        plus[c] += 1
                    if abs(delta + row[c]) < EPSILON:
                        minus[c] += 1
            if pairs < 2:
                continue
            signs = {c: (1 if plus[c] >= minus[c] else -1) for c in range(width) if c != balance and (plus[c] or minus[c])}
            explained = 0
            for i in range(len(values) - 1):
                found = _delta(values, i, balance, descending)
                if found is None:
                    continue
                delta, row = found
                net = sum((signs.get(c, 1) * row[c] for c in range(width) if c != balance and row[c] is not None), Decimal(0))
                if abs(delta - net) < EPSILON:
                    explained += 1
            if explained < 2 or explained / pairs < 0.6 or not signs:
                continue
            roles = Roles(balance, signs, descending)
            key = (explained, -balance)
            if best is None or key > best[0]:
                best = (key, roles)
    return best[1] if best else None


@dataclass(slots=True)
class Txn:
    page: int
    #: Position within its page.
    index: int
    net: Decimal | None
    balance: Decimal | None
    money_in: Decimal
    money_out: Decimal


def txn_from(page: int, index: int, row: Row, roles: Roles) -> Txn:
    amounts = [(c, v) for c, v in enumerate(row) if c != roles.balance and v is not None and c in roles.signs]
    net = sum((roles.signs[c] * v for c, v in amounts), Decimal(0)) if amounts else None
    money_in = sum((max(Decimal(0), roles.signs[c] * v) for c, v in amounts), Decimal(0))
    money_out = sum((max(Decimal(0), -roles.signs[c] * v) for c, v in amounts), Decimal(0))
    return Txn(page, index, net, row[roles.balance], money_in, money_out)


@dataclass(slots=True)
class Relation:
    """One row's balance against its neighbour's."""

    row: Txn  # the row whose amounts the relation uses
    other: Txn
    expected: Decimal
    printed: Decimal

    @property
    def holds(self) -> bool:
        return abs(self.expected - self.printed) < EPSILON


def _relations(txns: Sequence[Txn], descending: bool) -> list[Relation]:
    out: list[Relation] = []
    for first, second in zip(txns, txns[1:]):
        if first.balance is None or second.balance is None:
            continue
        if descending:
            # Newest first: the row above is the row below plus its own amounts.
            out.append(Relation(first, second, second.balance + (first.net or Decimal(0)), first.balance))
        else:
            out.append(Relation(second, first, first.balance + (second.net or Decimal(0)), second.balance))
    return out


@dataclass(slots=True)
class Audit:
    issues: dict[int, list[ValidationIssue]] = field(default_factory=dict)
    #: `ok`, `broken`, or `unchecked` when no column could be shown to be a running balance.
    balance: str = "unchecked"
    checked: int = 0
    broken: int = 0
    opening: Decimal | None = None
    closing: Decimal | None = None
    money_in: Decimal = Decimal(0)
    money_out: Decimal = Decimal(0)
    #: What the page prints under its rows, by the direction its column moves the balance.
    stated_in: Decimal | None = None
    stated_out: Decimal | None = None


def audit(
    txns: Sequence[Txn],
    roles: Roles | None,
    printed: dict[int, Decimal],
    labels: Sequence[str],
    sums: dict[int, Decimal],
    first_page: int,
) -> Audit:
    """
    Hold the statement to its own arithmetic.

    `printed` is what the page prints under its rows, by money column;
    `sums[c]` is what the rows of that column add up to; `labels` are the
    titles as printed, used only to say which column a message is about.
    """
    report = Audit()
    if roles is None or not txns:
        return report
    issues = report.issues

    def raise_issue(page: int, code: str, message: str, rows: list[int]) -> None:
        issues.setdefault(page, []).append(ValidationIssue(code, message, rows))

    relations = _relations(txns, roles.descending)
    report.checked = len(relations)
    report.broken = sum(not r.holds for r in relations)
    report.balance = "broken" if report.broken else "ok"

    position = 0
    while position < len(relations):
        relation = relations[position]
        if relation.holds:
            position += 1
            continue
        following = relations[position + 1] if position + 1 < len(relations) else None
        if following is not None and not following.holds:
            # Two breaks in a row share a row, and it is that row's own balance
            # that was misread: it fits neither neighbour.
            shared = {id(relation.row), id(relation.other)} & {id(following.row), id(following.other)}
            for each in (relation.row, relation.other):
                if id(each) in shared:
                    raise_issue(
                        each.page,
                        "bank-balance",
                        f"Row {each.index + 1} (page {each.page}): its balance does not fit the rows on either side.",
                        [each.index],
                    )
            position += 2
            continue
        target = relation.row
        raise_issue(
            target.page,
            "bank-balance",
            f"Row {target.index + 1} (page {target.page}): the printed balance "
            f"{format_money(relation.printed)} is off by {format_money(abs(relation.printed - relation.expected))} "
            f"from the neighbouring row's {format_money(relation.other.balance or Decimal(0))} and this row's amounts.",
            [target.index],
        )
        position += 1

    report.money_in = sum((t.money_in for t in txns), Decimal(0))
    report.money_out = sum((t.money_out for t in txns), Decimal(0))

    for column, total in printed.items():
        got = sums.get(column)
        if got is None or abs(abs(total) - got) < EPSILON:
            continue
        title = labels[column] if column < len(labels) and labels[column] else f"column {column + 1}"
        raise_issue(
            first_page,
            "bank-totals",
            f"The figure printed under '{title}' is {format_money(abs(total))}; the rows add up to {format_money(got)}.",
            [],
        )
        # What the page prints, by the direction the column moves the balance.
    for column, total in printed.items():
        sign = roles.signs.get(column)
        if sign == 1 and report.stated_in is None:
            report.stated_in = abs(total)
        elif sign == -1 and report.stated_out is None:
            report.stated_out = abs(total)

    first, last = txns[0], txns[-1]
    if roles.descending:
        report.closing = first.balance
        report.opening = last.balance - (last.net or Decimal(0)) if last.balance is not None else None
    else:
        report.opening = first.balance - (first.net or Decimal(0)) if first.balance is not None else None
        report.closing = last.balance
    return report
