# OCR reader

Reads a table out of a PDF, a scan or a photograph — an invoice, an order, a lottery
retailer's weekly paperwork, a bank statement — and returns its rows as JSON, keyed by the
column titles the page itself prints:

```json
{
  "kind": "table",
  "headers": ["Game", "Name", "Int", "Rec", "Act", "Set"],
  "rows": [
    {
      "Game": "815",
      "Name": "$1,000,000 JACKPOT",
      "Int": "000",
      "Rec": "002",
      "Act": "000",
      "Set": "001"
    }
  ]
}
```

Nothing about a page is built in: there is no catalog of games or fields, no fixed column
names and no fixed number of columns. The page's own titles are the schema (a page that
prints none has blank titles, and the export names those columns by their place). Every row
that is read is kept; a figure that does not agree with the page's own arithmetic is flagged,
never dropped.

**You say what the document is.** The app has a *Document type* choice — receipt / invoice,
lottery, or bank statement — and the choice picks the reader. Nothing guesses, and changing
it clears what was uploaded.

## Layout

```
python/reader/   the reader: PDFs, recognised pages, columns, rows, checks
python/          the server (serve.py) and the PP-OCR engine (read_receipt.py)
frontend/        the web app (Vite). Build output is frontend/dist
```

**All the reading happens in `python/reader/`.** The browser posts a file to
`/document` and draws what comes back; it does not parse PDFs, recognise text, or build
rows. There is one implementation of every rule, so a fix lands once.

## Quick start

From the repo root:

```bash
pnpm --dir frontend install
# one-time Python setup: see python/README.md
.venv/bin/python python/serve.py --warm   # the PP-OCR reader, on 127.0.0.1:8756
pnpm --dir frontend dev                    # the app, on http://localhost:5173
```

The page calls `/document`, `/lottery`, `/bank`, `/export` and `/page` on its own host. Vite
forwards those to the Python server.
`serve.py` is the reader: without it the app says so and reads nothing, because there is no
second reader to fall back to. See [`python/README.md`](python/README.md) for the engine and
its scores.

```bash
pnpm test                    # the app's tests and the reader's
pnpm --dir frontend build    # typecheck + production build
pnpm corpus                  # the reader, against every document you keep
```

The reader must be running for the app to read anything: `pnpm reader` starts it. Without
it the app says so instead of falling back to a less accurate reader, because there is no
longer a second one.

### The regression corpus

The reader takes the layout from the page rather than from a template, so a rule that
squares a column on one invoice decides a different column on the next. A change therefore
cannot be judged on the document that prompted it.

Put the documents you care about in `frontend/tools/corpus/` — it is excluded from the
repository, like `public/samples/`, because they are whole invoices — and
[`python/check_corpus.py`](python/check_corpus.py) records how each one reads. After that,
any change that moves a row on any of them is reported with the rows that moved, so an
improvement on one form can be told apart from a regression on another.

```bash
.venv/bin/python python/check_corpus.py                 # what moved?
UPDATE_CORPUS=1 .venv/bin/python python/check_corpus.py # record it
```

It is a diff, not a verdict: a failure may be exactly the change you wanted. With no corpus
the test skips, so a fresh checkout still runs green.

## Put it on a server

The server needs Python 3.12 and Node (to build the frontend). Upload this repo, then:

```bash
pnpm --dir frontend install
pnpm --dir frontend build
# one-time: see python/README.md for the 3.12 virtualenv
uv pip install -r python/requirements.txt --excludes python/excludes.txt
.venv/bin/python python/serve.py --live --warm
```

`--live` listens on `0.0.0.0:8080` and serves `frontend/dist` together with the reader's own routes.
Receipts uploaded there are read on that machine.

Because that port faces a network, `--live` asks for a key, and prints one it
generated if you did not give it one:

```
key: 9bT1s_4xQw…
  open the app:   http://<server>:8080/?key=9bT1s_4xQw…
  from a script:  -H "Authorization: Bearer <key>"
```

Open that URL once. The key moves into a cookie, the address bar goes back to
`/`, and the app works normally from then on — nothing to paste again. Set your
own with `--token` (or `OCR_READER_TOKEN`) so it survives a restart. `--open`
serves with no key at all, for a network you trust and nothing else.

On localhost — `pnpm reader`, and the Vite dev server in front of it — no key
is asked for, because the only callers are on the same machine.

Stop it with `lsof -ti tcp:8080 | xargs kill`. Pass `--port` to use another port.
If a reverse proxy terminates HTTPS, forward the `Host` header. If the page is
hosted on a different origin than the API, build the frontend with
`VITE_DOC_READER_URL=https://api.example.com` and start the reader with
`--origin https://app.example.com` — and put that reader behind your own proxy,
since the cookie hand-off only reaches a browser the reader itself serves.

One read runs at a time (the recogniser's model is not re-entrant), 8 wait in
line, and past that the reader answers 503 so the upload can be retried. To
serve more than one person at a time, run more than one reader behind a
balancer rather than raising the queue.

## How it works

```
browser: the file → POST /document, /lottery or /bank (the document type you chose)
python:  PDF text layer, or watermark suppression → PP-OCR → word boxes
         → glyphs joined, each word cut to its ink → columns → rows → the page's own checks
browser: draws the rows, and GET /page for the picture of each PDF page
         → POST /export for the CSV or JSON, shaped by the reader
```

A caller with nothing to edit can skip the middle step entirely:

```bash
curl -s --data-binary @invoice.pdf 'http://127.0.0.1:8756/document?format=csv'
curl -s --data-binary @invoice.pdf 'http://127.0.0.1:8756/document?format=json'
```

A page is read from the PDF's own text where it has one, because that text is exact and a
recogniser's is not; a scan or a photograph is recognised, and only those pages pay for it.

- **`reader/columns.py`** is the general table reader: it takes a table's layout from the
  page — the printed titles say which column is which, and the rows say where one ends and
  the next begins. Any table, in any layout.
- **`reader/lottery/`** reads a lottery page as the table it holds. It assumes no layout: rows
  are found from where the page's figures line up, columns from the positions most rows
  start or end a word at, titles from what is printed over them. A totals row is the row
  whose figures equal the others' sums, found by arithmetic, not by what it is called.
- **`reader/bank/`** reads a bank statement: rows start at a date, which column is the running
  balance and which way each other column moves it is worked out from the arithmetic, and
  every row is checked against the balance beside it.
- **`reader/glyphs.py`** joins the glyphs a recogniser returns one at a time (on a
  line-printer face `144732` comes back as `1 4 4 7 3 2`), then cuts each word's box to its
  own ink, so the gaps between words and columns are real, as they are in a PDF.
- **`reader/validate.py`** holds a reading to its own arithmetic without knowing what
  anything is called: a totals row against the sums, a count stated under the rows against
  the rows read, a figure printed twice, a total against the lines above it.
- **Several tables on a page** are read as several tables. An invoice prints its items, and
  under them something like `Previous Balances` with titles of its own; reading the second
  under the first's columns filed its dates as descriptions. Each extra table is shown under
  the main one and downloads as its own CSV, and the document's JSON carries them in `tables`.
- **Skipped & Removed** is the second export, kept apart from the data one. It logs every
  printed line the reading left out — totals, page furniture, a line that belongs to no
  item, a note such as `OUT OF STOCK` cut out of an item's description — and any page that
  produced no rows, each with its page, reason and confidence.

## Where the code lives

```
python/
  reader/
    boxes.py            the word box, and grouping words into lines
    pdf_text.py         a PDF's own text, and rendering its pages
    glyphs.py           joining a recogniser's glyphs, and cutting each word to its ink
    dates.py            dates as a page prints them, and as a recogniser misreads them
    columns.py          the general table reader, off a page's printed titles
    skipped.py          what a line the table reader left out actually is
    lottery/            a lottery page as the table it holds: rows, columns, checks
    bank/               a bank statement: rows at dates, balance found by arithmetic
    validate.py         a reading against the page's own arithmetic
    assemble.py         the general reading of a page, and its result type
    document.py         a whole file in, every page's reading out
    export.py           every page as one CSV or JSON, tables and log beside it
  tests/                the readers and the export, case by case
  serve.py              the server: POST /document, /lottery, /bank, /export, GET /page
  read_receipt.py       the PP-OCR engine
  check_corpus.py       every document you keep, against how it last read
frontend/src/
  App.tsx               upload, result table, JSON copy/download
  components/           React UI
  lib/                  downloads, theme, progress state
  components/row-status.ts  which rows still want a look
  ocr/
    types.ts            what the reader returns — depends on nothing
    api.ts              /document, /page, /export
    result.ts           listing a page's cells and editing one
```
