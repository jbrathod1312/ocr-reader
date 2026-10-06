# How the Python reader and the frontend work together

What happens when you upload a receipt, which side does what, and how the
server differs from the command-line script.

## Short summary

- **All the reading is Python.** `python/reader/` opens the PDF, recognises
  the pages that need it, finds the columns, builds the rows and runs the
  receipt's own arithmetic checks. The browser does none of it.
- The app posts the file to **`POST /document`** — the bytes as they are, not
  a multipart form, and not a third-party cloud service. The server tells a
  PDF from an image by looking at the first five bytes.
- The server is **`python/serve.py`** (default **127.0.0.1:8756**). In dev,
  Vite proxies `/document`, `/lottery`, `/bank`, `/export`, `/page` and `/health` to that port.
- **There is no fallback.** Without the reader the app says so and reads
  nothing: the browser has no reader of its own any more.
- **`read_receipt.py`** is the PP-OCR engine. `serve.py` imports it; it also
  runs from the command line to dump word boxes for a single image.

---

## The routes

| Route            | What it is for                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /document` | A PDF or an image in; every page's reading out. `?format=csv` or `?format=json` returns the finished export instead, for a caller with nothing to edit. |
| `POST /lottery`  | A lottery page in; the same shape out as `/document`. Read by `reader/lottery/`, which assumes no layout. |
| `POST /bank`     | A bank statement in; the same shape out as `/document`, plus a `statement` summary. Read by `reader/bank/`, not by the general reader. |
| `POST /export`   | The pages a caller holds, back as one CSV or JSON.                                                                                                      |
| `GET /page`      | The picture of one page of a PDF just read, at the width asked for.                                                                                     |

`GET /health` says whether the reader is up, which the app asks on start-up so
it can warn before a file is chosen. It also reports `keyNeeded` and
`authorised`, so "no reader" and "no key" do not look the same from outside.

### The key

On localhost there is none: the only callers are on this machine. `--live`
binds every interface, so it asks for one and generates it if you did not give
it one (`--token`, `OCR_READER_TOKEN`, or `--open` to serve without). A caller
presents it as `Authorization: Bearer <key>`; a browser opens the app once as
`/?key=<key>` and the reader moves it into an HttpOnly cookie and redirects to
the clean URL. The cookie is what makes the viewer work — `<img src="/page…">`
cannot carry a header. `/health` is the one route that never asks.

### A page as pixels reads like the same page as text

A recogniser boxes a whole line and shares the box out among its words, so
neighbouring words touch and the gaps that tell one column from the next are
gone; a PDF's words have real gaps. After the glyphs are joined, each word's box
is cut down to its own ink (`glyphs.tighten_boxes`), putting the gaps back, so the
readers above see the same geometry either way. A blank run between two digits is
a space only if it is wide (`DIGIT_GAP`): the digits of one number are spaced just
over what separates two words.

### Three readers, chosen by the user

The app has a "Document type" choice above the viewer, and the choice picks the
route. Nothing works out what a document is:

| Choice | Route | Reader |
| --- | --- | --- |
| Receipt / invoice | `/document` | `reader/assemble.py` — the tables printed on a page, and nothing else. A page with no table is an empty table. |
| Lottery | `/lottery` | `reader/lottery/` — the table a lottery page holds, found from where its figures sit and checked against its own arithmetic. Assumes no layout, columns or titles. |
| Bank statement | `/bank` | `reader/bank/` — see below. |

The general reader used to try the lottery's three layouts as well, and a page
that was not a table at all came back as an `inventory` with no rows. It no longer
does: lottery pages are read by `reader/lottery/`, which has no layouts to try.
`python/check_corpus.py` reads `frontend/tools/corpus/lottery/` and `bank/` with
their own readers.

### The lottery reader assumes nothing about the page

It is not told that a page is an inventory, a list of settlements or an invoice,
how many columns a table has, or what they are called. It finds the table the
page holds and shows it; a page with its labels in another language, or none,
reads the same.

- **Rows** are found from the page's figures. Figures are clustered by where they
  sit; a cluster that many words fall in is a column of figures, and a figure
  belongs to it only if it is made of the same kinds of characters (a time is not
  an amount). Each vertical position that has such a figure is a row.
- **Columns** are the positions most rows line up on — where a word starts, or,
  for right-aligned figures, where it ends. The number of columns is whatever the
  rows show. A run of digits wide enough to span several columns is cut into
  them; figures that drift sideways down a photograph go to the nearest column.
- **Titles** are what the page prints directly over the columns, copied as
  printed; a page that prints none has blank titles, and the export names those
  columns by their place (`Column 3`). The page's **title** is the nearest line
  above the table made of words, if the recogniser was sure of it.
- **Repairs** are by shape only, and by what a column is made of: dots between
  groups of three are thousands, a letter in the middle of digits is a digit, `S`
  before digits is `$` in a column that uses `$`, a misread date is put right in a
  column of dates.
- **Checks** are arithmetic. The totals row is the row whose figures equal the
  other rows' sums in at least two columns; a count stated under the rows is held
  against the rows read (only if it could be one); two figures under the same
  label that differ by a single digit, or a total one digit off the lines above it,
  are flagged.

The result is always a table: nothing says what sort of document it is. The
recogniser holds no lottery format either — it cuts a line only at whitespace.

### Bank statements

A statement is read by its own reader, on its own route, **because the user
says it is one** — the app has a "Document type" choice above the viewer and
nothing guesses. Changing it clears whatever was uploaded, which was read as the
old kind.

`reader/bank/` reads **no word for its meaning** — no title, no label, no
"Viewing … of N", no `CR`/`DR`. Everything is shape, position or arithmetic, so a
statement with its titles in another language, or none at all, reads the same:

- **Rows** are the lines that carry a date and a figure (a date and a figure are
  recognised by their shape). A link above a date line and a second line of the
  description below it belong to that row.
- **Columns** are where those rows' ink gathers: figures are clustered by where
  they sit, dates by the cluster the rows anchor on, and text by the clear space
  the rows leave between it.
- **Titles** are copied as printed from the nearest line over the columns; none
  is interpreted. With no such line the columns are `Column 1`, `Column 2`…
- **Which column is the running balance, and which way each other column moves
  it, is found by arithmetic.** The balance column is the one whose changes from
  row to row equal the other columns' figures. A column whose figures add to the
  balance is money in; one whose figures take away is money out.
- **Totals** are figures printed under the rows with nothing in the balance
  column. They are held against what the rows add up to.

Row by row, each balance must follow from the one beside it, so a misread digit
breaks the proof and names the row to look at. The checks come back as ordinary
validation issues (`bank-balance`, `bank-totals`), so the viewer flags the rows
and lists the warnings as it does for any page. Where no column can be shown to
be a running balance the statement is reported as unchecked, not guessed at.
`python/check_corpus.py` reads `frontend/tools/corpus/bank/` with this reader.

### Why `/export` is a POST that carries the rows

The export is of what is **on screen**, not of what was read. Somebody may have
typed into a cell or taken an extra table out of the export, and those live in
the browser. A `GET` that re-read the file would quietly hand back the original
reading and lose the corrections, so the app posts its pages back and the
reader shapes them.

A caller that has nothing to change skips the middle step:

```bash
curl -s --data-binary @invoice.pdf 'http://127.0.0.1:8756/document?format=csv'
```

### Why `/page` exists

The browser never opens the PDF, so it has no pixels to show. The viewer's
picture, the thumbnail strip, the text overlay drawn over it, the zoom and the
download all need a rendered page, and only the reader can produce one. An
uploaded **image** is its own picture and is never asked for back.

---

## What reads what

```
browser: the file → POST /document, /lottery or /bank (the document type you chose)
python:  a PDF's text layer, or watermark suppression → PP-OCR → word boxes
         → glyphs joined, each word cut to its ink → columns → rows → the page's own checks
browser: draws the rows, and GET /page for the picture of each PDF page
         → POST /export for the CSV or JSON, shaped by the reader
```

A page is read from the PDF's own text where it has one, because that text is
exact and a recogniser's is not. A scan, a photograph, or a PDF whose text
layer is a token has to be recognised, and only those pages pay for it.

### Inside the reader

| Module               | What it does                                                          |
| -------------------- | --------------------------------------------------------------------- |
| `reader/boxes.py`    | The word box, and grouping words into printed lines.                  |
| `reader/pdf_text.py` | A PDF's own text, and rendering its pages to pixels.                  |
| `reader/glyphs.py`   | Joins the glyphs a recogniser returns one at a time, then cuts each word to its ink. |
| `reader/dates.py`    | Dates as a page prints them, and as a recogniser misreads them.       |
| `reader/columns.py`  | The general table reader: a table, read off its printed titles.       |
| `reader/skipped.py`  | What a line the table reader left out actually is.                    |
| `reader/lottery/`    | A lottery page as the table it holds: rows and columns from the figures, checks. |
| `reader/bank/`       | A bank statement: ledger, rows at dates, the running-balance checks.  |
| `reader/validate.py` | A reading against the page's own arithmetic.                          |
| `reader/assemble.py` | The general reading of a page, and the result type every reader returns. |
| `reader/document.py` | A whole file in, every page's reading out.                            |
| `reader/export.py`   | Every page as one CSV or JSON, tables and log beside it.              |

---

## Two Python entry points

| Script                                             | Purpose                                                                                 |
| -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `python/serve.py`                                  | The HTTP server the app talks to.                                                       |
| `python/read_receipt.py`                           | The PP-OCR engine. Also a command-line tool: an image path in, word-box JSON on stdout. |

They share the watermark suppression and the recogniser; `serve.py` adds
everything in `reader/` on top of the words.

---

## What the browser still does

Rendering, and the two things that are the person's rather than the reader's:

- **Edits.** A cell you type into is held in the page and sent back with the
  export.
- **Dropped tables.** An extra table you remove is remembered by key and left
  out of the export's `tables`.
- **Removed rows.** A row you remove (after confirming) is taken out of the page's
  reading and so out of the export; *Reset edits & rows* brings the page back.
- **The document type.** Which reader runs is the person's choice, and changing it
  clears what was uploaded.

Everything else it draws — rows, headers, the validation notes, the log of what
was left out — arrives from the reader already shaped.

---

## Checking a change

The reader takes a table's layout from the page rather than from a template, so
a rule that squares a column on one invoice decides a different column on the
next. A change cannot be judged on the document that prompted it:

```bash
pnpm test      # the app's tests and the reader's
pnpm corpus    # every document in frontend/tools/corpus/, against how it last read
```

`pnpm corpus` prints the rows that moved, so an improvement on one form can be
told apart from a regression on another. `UPDATE_CORPUS=1` records a new
reading once it has been judged.

A Python change needs the reader restarted — Vite only hot-reloads the
frontend.
