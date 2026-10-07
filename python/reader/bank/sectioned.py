"""
A statement that balances by a summary box, not a running balance.

The ledger reader in `statement` wants a balance printed beside every row, and
works out the rest from how that balance moves. A great many statements are not
built that way. This one — the format a `RENASANT BANK` commercial-checking
statement is printed in, and a common one — proves itself differently: a box at
the head states the previous balance, the month's additions and subtractions and
the ending balance, and the body is three lists under their own banners —

    *************  CHECKS  *************
    *********  OTHER DEBITS  **********
    ***********  CREDITS  ************

The checks and the other debits are what was subtracted; the credits are what was
added; and the reading is right when the lists add up to the box and the box adds
up to itself: `previous + additions − subtractions = ending`.

Unlike the ledger reader, this one reads the banners and the box labels for what
they say, because that is what a template *is*: it is told, bank by bank, what
this statement calls its sections and its totals. It is chosen for a page only
when the page carries these banners, so a statement of another shape is read by
another reader. What it does not do is invent a figure: every amount shown is one
the page prints, and the box's own arithmetic is the check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from ..assemble import OcrResult
from ..boxes import WordBox, group_into_lines, line_band
from ..columns import SkippedLine, TableRow
from ..document import PageReading
from .money import format_money, is_money, parse_money

TITLE = "Bank statement"

#: A date as this statement prints one in its lists: `05-01`, or `05/01`.
_SHORT_DATE = re.compile(r"^\d{1,2}[-/]\d{1,2}$")
#: A check number: four to seven digits, and nothing else.
_CHECK_NUMBER = re.compile(r"^\d{4,7}$")
#: A banner rule: three or more of the asterisks the statement sets its
#: section titles between. The title is whatever words sit among them.
_BANNER = re.compile(r"\*{3,}")

#: The three sections, by the word the banner prints. Order is the order they
#: are shown in, which is the order the statement prints them.
CHECKS, DEBITS, CREDITS = "checks", "debits", "credits"
_SECTION_TITLES = {
    CHECKS: "Checks",
    DEBITS: "Other Debits",
    CREDITS: "Credits",
}

#: What the summary box calls the four figures the statement balances by. Read
#: for their meaning, which is the whole point of a template: another bank's box
#: would be given its own words here.
_BOX = {
    "previous": "PREVIOUS BALANCE",
    "additions": "ADDITIONS",
    "subtractions": "SUBTRACTIONS",
    "ending": "ENDING BALANCE",
}

EPSILON = Decimal("0.005")


# --------------------------------------------------------------------------- #
# Telling this statement apart                                                 #
# --------------------------------------------------------------------------- #


def _banner_section(text: str) -> str | None:
    """Which section a banner line announces, or None where it is not one."""
    if not _BANNER.search(text):
        return None
    upper = text.upper()
    if "OTHER DEBITS" in upper or re.search(r"\bDEBITS\b", upper):
        return DEBITS
    if "CREDITS" in upper:
        return CREDITS
    if "CHECKS" in upper:
        return CHECKS
    return None


def looks_sectioned(pages: Sequence[Any]) -> bool:
    """
    Whether these pages are a statement this reader is for.

    The signature is the banners themselves: a page that prints a CHECKS,
    OTHER DEBITS or CREDITS banner over its lists, and a summary box that states
    a previous and an ending balance. Two of the three banners, or one banner
    and the box, is enough — a statement does not always use all three.
    """
    banners: set[str] = set()
    has_box = False
    for page in pages:
        for line in group_into_lines(list(page.words), 0.5):
            text = " ".join(w.text for w in sorted(line, key=lambda w: w.x))
            section = _banner_section(text)
            if section is not None:
                banners.add(section)
            upper = text.upper()
            if _BOX["previous"] in upper or _BOX["ending"] in upper:
                has_box = True
    return len(banners) >= 2 or (len(banners) >= 1 and has_box)


def bank_name(pages: Sequence[Any]) -> str | None:
    """
    The bank's name as the letterhead prints it, for the summary to show.

    The first all-capitals line of the first page that reads like a name —
    letters and spaces, two words or more, and not one of the headings the
    statement prints. Nothing is keyed to a particular bank; this only reports
    what the page prints at its head.
    """
    if not pages:
        return None
    lines = group_into_lines(list(pages[0].words), 0.5)
    for line in sorted(lines, key=lambda line: line_band(line).top)[:12]:
        text = " ".join(w.text for w in sorted(line, key=lambda w: w.x)).strip()
        letters = text.replace(" ", "")
        if len(text.split()) < 1 or not letters.isalpha() or text != text.upper():
            continue
        if any(word in text for word in ("STATEMENT", "ACCOUNT", "CHECKING", "SAVINGS", "PAGE")):
            continue
        return text.title()
    return None


# --------------------------------------------------------------------------- #
# The rows of each section                                                     #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Row:
    cells: list[str]
    amount: Decimal | None
    page: int
    confidence: float


@dataclass(slots=True)
class _Section:
    key: str
    headers: list[str]
    rows: list[_Row] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return sum((abs(r.amount) for r in self.rows if r.amount is not None), Decimal(0))


def _confidence(words: Sequence[WordBox]) -> float:
    return sum(w.confidence for w in words) / len(words) if words else 1.0


def _last_money(words: Sequence[WordBox]) -> WordBox | None:
    """The rightmost figure on a line: a list sets its amount at the right."""
    for word in sorted(words, key=lambda w: w.x, reverse=True):
        if is_money(word.text):
            return word
    return None


def _check_runs(words: Sequence[WordBox]) -> list[list[WordBox]]:
    """
    A CHECKS line cut into its runs, each `number [*] date amount`.

    The statement sets two of these side by side on a line, and the legend
    `* SKIP IN CHECK SEQUENCE` after the last of them; a run begins at a check
    number and takes the words up to the next, so the legend, which starts at no
    number, is left behind.
    """
    ordered = sorted(words, key=lambda w: w.x)
    runs: list[list[WordBox]] = []
    for word in ordered:
        if _CHECK_NUMBER.match(word.text):
            runs.append([word])
        elif runs:
            runs[-1].append(word)
    return [run for run in runs if _last_money(run) is not None]


def _check_row(run: Sequence[WordBox], page: int) -> _Row:
    amount = _last_money(run)
    number = run[0].text + (" *" if any(w.text == "*" for w in run) else "")
    date = next((w.text for w in run if _SHORT_DATE.match(w.text)), "")
    return _Row([number, date, amount.text if amount else ""], parse_money(amount.text) if amount else None, page, _confidence(run))


def _list_row(line: Sequence[WordBox], page: int) -> _Row:
    """A row of OTHER DEBITS or CREDITS: date, description, the figure at the right."""
    ordered = sorted(line, key=lambda w: w.x)
    amount = _last_money(ordered)
    date = ordered[0].text
    middle = [w for w in ordered[1:] if w is not amount]
    description = " ".join(w.text for w in middle).strip()
    return _Row([date, description, amount.text if amount else ""], parse_money(amount.text) if amount else None, page, _confidence(ordered))


def _append_description(row: _Row, line: Sequence[WordBox]) -> None:
    """A continuation line carries no date; it is the row above's description going on."""
    extra = " ".join(w.text for w in sorted(line, key=lambda w: w.x)).strip()
    if extra:
        row.cells[1] = (row.cells[1] + " " + extra).strip()


def _is_column_head(text: str) -> bool:
    upper = text.upper()
    return "DESCRIPTION" in upper or ("NUMBER" in upper and "AMOUNT" in upper) or (
        "DATE" in upper and ("SUBTRACTIONS" in upper or "ADDITIONS" in upper or "AMOUNT" in upper)
    )


@dataclass(slots=True)
class _PageRead:
    page: Any
    sections: dict[str, list[_Row]]
    #: The lines this page's reading left out, each with why.
    skipped: list[SkippedLine]


def _read_page(page: Any) -> _PageRead:
    """One page cut into its sections' rows, by the banners it prints."""
    found: dict[str, list[_Row]] = {CHECKS: [], DEBITS: [], CREDITS: []}
    skipped: list[SkippedLine] = []
    section: str | None = None
    current: _Row | None = None
    lines = sorted(group_into_lines(list(page.words), 0.5), key=lambda line: line_band(line).top)
    for line in lines:
        ordered = sorted(line, key=lambda w: w.x)
        text = " ".join(w.text for w in ordered)
        band = line_band(line)
        left = min(w.x for w in ordered)
        banner = _banner_section(text)
        if banner is not None or _BANNER.search(text):
            section = banner
            current = None
            continue
        if section is None or _is_column_head(text):
            continue
        if section == CHECKS:
            runs = _check_runs(ordered)
            if runs:
                found[CHECKS].extend(_check_row(run, page.number) for run in runs)
            else:
                skipped.append(SkippedLine("furniture", text, _confidence(ordered), band.top, left))
            current = None
        else:
            if _SHORT_DATE.match(ordered[0].text) and _last_money(ordered) is not None:
                current = _list_row(ordered, page.number)
                found[section].append(current)
            elif current is not None:
                _append_description(current, ordered)
            else:
                skipped.append(SkippedLine("furniture", text, _confidence(ordered), band.top, left))
    return _PageRead(page, found, skipped)


# --------------------------------------------------------------------------- #
# The summary box                                                              #
# --------------------------------------------------------------------------- #


def _read_box(pages: Sequence[Any]) -> dict[str, Decimal]:
    """The figures the summary box states, by what it calls them."""
    box: dict[str, Decimal] = {}
    for page in pages:
        for line in group_into_lines(list(page.words), 0.5):
            text = " ".join(w.text for w in sorted(line, key=lambda w: w.x))
            upper = text.upper()
            for key, label in _BOX.items():
                if key in box:
                    continue
                match = re.search(re.escape(label) + r"\s*[-+]?\s*(\(?\$?[\d,]+\.\d{2}\)?)", upper)
                if match:
                    value = parse_money(match.group(1))
                    if value is not None:
                        box[key] = value
    return box


# --------------------------------------------------------------------------- #
# The reading                                                                  #
# --------------------------------------------------------------------------- #


def _table(section: _Section, rows: Sequence[_Row]) -> dict[str, Any]:
    return {
        "title": _SECTION_TITLES[section.key],
        "headers": list(section.headers),
        "rows": [{"cells": list(r.cells), "confidence": r.confidence, "label": False} for r in rows],
        "columnBounds": [],
    }


def _result(page_rows: dict[str, list[_Row]], sections: dict[str, _Section], word_count: int, skipped: list[SkippedLine]) -> OcrResult:
    """
    One page's reading: the sections printed on it, each a titled table.

    A page carries one or two of the statement's lists. The first is the page's
    own table — the one the viewer shows under the table's own title and the row
    counter counts. Every list on the page, the first included, is also carried
    as a titled table, so a list that is one page's own and the next page's
    continuation is gathered whole for the export under its own title; the viewer
    leaves out the one that is already the page's own, so nothing shows twice. A
    page with none of the lists — a check or deposit image, the back of the
    statement — has an empty table and says so.
    """
    present = [key for key in (CHECKS, DEBITS, CREDITS) if page_rows[key]]
    if not present:
        return OcrResult(
            kind="table",
            title=TITLE,
            headers=[],
            table_rows=[],
            tables=[],
            column_bounds=None,
            validation=[],
            skipped=sorted(skipped, key=lambda s: s.y),
            warnings=[],
            word_count=word_count,
            body_left=None,
        )

    primary = sections[present[0]]
    primary_rows = page_rows[present[0]]
    table_rows = [TableRow(cells=list(r.cells), confidence=r.confidence) for r in primary_rows]
    section_tables = [_table(sections[key], page_rows[key]) for key in present]
    return OcrResult(
        kind="table",
        title=_SECTION_TITLES[primary.key],
        headers=list(primary.headers),
        table_rows=table_rows,
        # tables[0] is the page's own, as for any page; tables[1:] are every
        # list on the page, the page's own among them, for the export to gather.
        tables=[_table(primary, primary_rows), *section_tables],
        column_bounds=None,
        validation=[],
        skipped=sorted(skipped, key=lambda s: s.y),
        warnings=[],
        word_count=word_count,
        body_left=None,
    )


def read_sectioned(pages: Sequence[Any]) -> tuple[list[PageReading], dict[str, Any]]:
    """Every page of a sectioned statement, read, and the summary it balances by."""
    reads = [_read_page(page) for page in pages]

    sections = {
        CHECKS: _Section(CHECKS, ["Number", "Date", "Amount"]),
        DEBITS: _Section(DEBITS, ["Date", "Description", "Subtractions"]),
        CREDITS: _Section(CREDITS, ["Date", "Description", "Additions"]),
    }
    for read in reads:
        for key, rows in read.sections.items():
            sections[key].rows.extend(rows)

    box = _read_box(pages)
    summary = _summary(pages, sections, box)

    # Whether any page of the document could be read: when one was, an empty
    # page is one the statement's lists do not reach — a deposit or check image
    # — and not a page nothing could read. Only a document no page of which was
    # read is one with no recogniser behind it.
    readable = any(getattr(page, "words", None) for page in pages)
    readings: list[PageReading] = []
    for read in reads:
        result = _result(read.sections, sections, len(read.page.words), read.skipped)
        if not any(read.sections.values()):
            result.warnings.append(
                "No statement rows were found on this page. It may be a check or deposit image, "
                "or the page may not be part of the statement's lists."
                if readable
                else "This page is a scan and no recogniser is available to read it."
            )
        readings.append(
            PageReading(
                number=read.page.number,
                width=read.page.width,
                height=read.page.height,
                reader=read.page.reader,
                result=result,
                words=list(read.page.words),
            )
        )
    return readings, summary


def _dates(sections: dict[str, _Section]) -> list[str]:
    seen: list[str] = []
    for key in (DEBITS, CREDITS, CHECKS):
        for row in sections[key].rows:
            index = 1 if key == CHECKS else 0
            if row.cells[index]:
                seen.append(row.cells[index])
    return sorted(seen)


def _summary(
    pages: Sequence[Any],
    sections: dict[str, _Section],
    box: dict[str, Decimal],
) -> dict[str, Any]:
    def money(value: Decimal | None) -> str | None:
        return format_money(value) if value is not None else None

    checks, debits, credits = sections[CHECKS], sections[DEBITS], sections[CREDITS]
    subtractions = checks.total + debits.total
    additions = credits.total
    transactions = sum(len(s.rows) for s in sections.values())

    previous, ending = box.get("previous"), box.get("ending")
    stated_add, stated_sub = box.get("additions"), box.get("subtractions")

    # The statement balances when the box adds up to itself, and when the lists
    # add up to the box. Each is checked only where the figures it needs were read.
    box_ok = (
        previous is not None
        and ending is not None
        and stated_add is not None
        and stated_sub is not None
        and abs(previous + stated_add - stated_sub - ending) < EPSILON
    )
    lists_add = stated_add is None or abs(additions - stated_add) < EPSILON
    lists_subtract = stated_sub is None or abs(subtractions - stated_sub) < EPSILON

    dates = _dates(sections)
    details = _details(pages)
    return {
        "kind": "sectioned",
        "bank": bank_name(pages),
        "transactions": transactions,
        "counts": {name: len(sections[key].rows) for key, name in _SECTION_TITLES.items()},
        "details": details,
        "firstDate": dates[0] if dates else None,
        "lastDate": dates[-1] if dates else None,
        "newestFirst": False,
        # What the rows of each kind add up to, and what the box states.
        "debits": money(subtractions),
        "credits": money(additions),
        "openingBalance": money(previous),
        "closingBalance": money(ending),
        # `ok` when the box's own figures balance, `broken` when they do not,
        # `unchecked` when the box was not fully read.
        "balanceCheck": "ok" if box_ok else ("unchecked" if previous is None or ending is None else "broken"),
        "balanceLinksChecked": 0,
        "balanceLinksBroken": 0,
        "stated": {
            "transactions": None,
            "debits": money(stated_sub),
            "credits": money(stated_add),
        },
        # The sectioned statement's own extra: the box figures, and whether the
        # lists add up to them.
        "box": {
            "previous": money(previous),
            "additions": money(stated_add),
            "subtractions": money(stated_sub),
            "ending": money(ending),
            "balances": box_ok,
            "additionsMatch": lists_add,
            "subtractionsMatch": lists_subtract,
        },
    }


#: The head fields this statement prints that are worth showing, by the label the
#: page prints and how its value sits: a figure or code to the right of the
#: label, or a date printed to its left (`MAY 31, 2026: THIS STATEMENT`). Read
#: for their meaning, as a template reads its own bank's words.
_DETAIL_RIGHT = [
    ("Account Number", "ACCOUNT NUMBER", r"(\d{6,})"),
    ("Avg Collected Balance", "AVG COLLECTED BALANCE", r"(\$?[\d,]+\.\d{2})"),
    ("Interest Earned YTD", "INTEREST EARNED YTD", r"(\$?[\d,]+\.\d{2})"),
]
_DETAIL_LEFT = [
    ("Last Statement", "LAST STATEMENT"),
    ("This Statement", "THIS STATEMENT"),
]
_PERIOD_DATE = r"([A-Z]+\s+\d{1,2},?\s*\d{4})"


def _details(pages: Sequence[Any]) -> list[dict[str, Any]]:
    """
    The head fields the statement states about the account, as printed.

    The box figures are the summary's; these are the rest of the head — the
    account number, the statement's dates, the average balance it reports — read
    by the labels this statement uses. Said once, in the order listed.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for page in pages:
        lines = [
            " ".join(w.text for w in sorted(line, key=lambda w: w.x))
            for line in group_into_lines(list(page.words), 0.5)
        ]
        blob = "\n".join(lines).upper()

        def add(label: str, value: str | None) -> None:
            if value and label not in seen:
                seen.add(label)
                out.append({"label": label, "value": value.strip(), "page": page.number})

        for label, marker in _DETAIL_LEFT:
            match = re.search(_PERIOD_DATE + r"\s*:?\s*" + marker, blob)
            add(label, match.group(1) if match else None)
        for label, marker, pattern in _DETAIL_RIGHT:
            match = re.search(re.escape(marker) + r"\s*[-+]?\s*" + pattern, blob)
            add(label, match.group(1) if match else None)
    return out
