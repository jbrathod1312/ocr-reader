import { Fragment, useMemo, useState } from 'react'

import {
  requestExport,
  type ExportPage,
  type ExtraTable,
  type StatementSummary,
} from '../ocr/api'
import { exportBaseName, saveFile } from '../lib/download'
import { ConfirmDialog } from './ConfirmDialog'
import { DownloadIcon } from './ExportButtons'
import { rowCells, toPublicJson } from '../ocr/result'
import {
  REVIEW_THRESHOLD,
  rowViews,
  type PageRows,
  type RowView,
} from './row-status'
import type { OcrResult } from '../ocr/types'

export type { PageRows } from './row-status'

/* -------------------------------------------------------------------------- */
/* Fields                                                                      */
/* -------------------------------------------------------------------------- */

interface FieldsViewProps {
  result: OcrResult
  /** Readings below this confidence are flagged for a human to check. */
  reviewThreshold?: number
  /** Rows the user has corrected by hand. */
  edited?: ReadonlySet<number>
  /** Rows the user has accepted as they read. */
  validated?: ReadonlySet<number>
  onEdit?: (rowIndex: number, cellIndex: number, value: string) => void
  /** Invoice or ticket heading, shown on the right of the toolbar. */
  title: string
  onResetEdits?: () => void
  /**
   * Every page read so far, when the document has several. A search looks
   * through all of them, not only the page on screen.
   */
  pages?: readonly PageRows[]
  /** Index of the page on screen within the document. */
  currentPage?: number
  /** Edit a row of any page: a search shows rows from all of them. */
  onEditPage?: (pageIndex: number, rowIndex: number, cellIndex: number, value: string) => void
  /** Take a row of any page out of the reading. */
  onRemoveRowPage?: (pageIndex: number, rowIndex: number) => void
  /** How many rows of the page on screen have been taken out. */
  removed?: number
  /** Show a page, from a search result's page number. */
  onOpenPage?: (pageIndex: number) => void
  /**
   * What a bank statement says about itself, when the document is one. Only
   * its columns are used here: a ledger's blanks and its running balance are
   * not what they are in a receipt, and the table draws them as what they are.
   */
  statement?: StatementSummary | null
}

const EMPTY = 'No table rows were read.'

/** Which rows the table is showing. */
type FilterMode = 'all' | 'flagged' | 'edited' | 'valid'

const FILTERS: { value: FilterMode; label: string }[] = [
  { value: 'all', label: 'All Fields' },
  { value: 'flagged', label: 'Needs Review' },
  { value: 'edited', label: 'Edited' },
  { value: 'valid', label: 'Validated' },
]

/** Rows shown before `View More`. About a screenful on a laptop. */
const PAGE_SIZE = 12

/** A cell that is a figure: digits with the marks a number is written with. */
const FIGURE = /^[$(-]?\d[\d,.:/]*\)?[A-Za-z]{0,2}%?$/

interface Profile {
  /** Whether each column holds figures. */
  numeric: boolean[]
  /** The text column with the longest cells, which should stay left-aligned. */
  wide: number
}

const PROFILES = new WeakMap<OcrResult, Profile>()

/**
 * What each column is made of, read from its cells and not from what it is
 * called: a column of figures lines up on the right, and the text column with
 * the longest readings is the one given room.
 */
function profileOf(result: OcrResult): Profile {
  const known = PROFILES.get(result)
  if (known) return known
  const rows = rowCells(result)
  const width = Math.max(result.headers.length, ...rows.map((row) => row.length), 0)
  const numeric: boolean[] = []
  let wide = 0
  let longest = -1
  for (let column = 0; column < width; column += 1) {
    const cells = rows.map((row) => (row[column] ?? '').trim()).filter(Boolean)
    const figures = cells.filter((cell) => cell.split(/\s+/).every((token) => FIGURE.test(token)))
    const isNumeric = cells.length > 0 && figures.length >= cells.length * 0.6
    numeric.push(isNumeric)
    const mean = cells.length ? cells.reduce((sum, cell) => sum + cell.length, 0) / cells.length : 0
    if (!isNumeric && mean > longest) {
      longest = mean
      wide = column
    }
  }
  const profile = { numeric, wide }
  PROFILES.set(result, profile)
  return profile
}

/** A column's class, which caps how wide its cells may grow. */
function columnClass(result: OcrResult, index: number): ColumnClass {
  const { numeric, wide } = profileOf(result)
  if (numeric[index]) return 'num'
  return index === wide ? 'col--wide' : 'col--text'
}

/**
 * The class of each column of a bare table, read from its cells the same way.
 *
 * The primary table profiles an `OcrResult`; an extra table carries only its
 * headers and rows, so it is profiled on those. Without this, every column but
 * the first was drawn as figures, and a `Description` column of sentences — the
 * widest thing on the page — overran the table and pushed its amount off the
 * edge. A column of figures lines up on the right; the longest text column is
 * the one given room to grow.
 */
function tableColumnClasses(headers: readonly string[], rows: readonly (readonly string[])[]): ColumnClass[] {
  const width = Math.max(headers.length, ...rows.map((row) => row.length), 0)
  const classes: ColumnClass[] = []
  let wide = 0
  let longest = -1
  for (let column = 0; column < width; column += 1) {
    const cells = rows.map((row) => (row[column] ?? '').trim()).filter(Boolean)
    const figures = cells.filter((cell) => cell.split(/\s+/).every((token) => FIGURE.test(token)))
    const numeric = cells.length > 0 && figures.length >= cells.length * 0.6
    classes.push(numeric ? 'num' : 'col--text')
    const mean = cells.length ? cells.reduce((sum, cell) => sum + cell.length, 0) / cells.length : 0
    if (!numeric && mean > longest) {
      longest = mean
      wide = column
    }
  }
  if (classes[wide] === 'col--text') classes[wide] = 'col--wide'
  return classes
}

type ColumnClass = 'num' | 'col--text' | 'col--wide'

/** Which column is the running balance, and which way each amount column moves it. */
interface Ledger {
  balance: number
  /** The dates column, or -1 where the rows carry none. */
  date: number
  /** By column: `in` adds to the balance, `out` takes from it. */
  amounts: ReadonlyMap<number, 'in' | 'out'>
}

/**
 * The statement's columns as the table draws them, or null for any other page.
 *
 * Null as well for a statement whose figures could not be shown to be a
 * ledger: the reader claims nothing about the columns then, and a table that
 * coloured them anyway would be claiming it for them. A statement's pages are
 * read under one set of columns, so one map serves every page on screen.
 */
const NO_AMOUNTS: ReadonlyMap<number, 'in' | 'out'> = new Map()

function ledgerOf(statement: StatementSummary | null | undefined): Ledger | null {
  const roles = statement?.columns
  if (!roles || roles.balance === null) return null
  const amounts = new Map<number, 'in' | 'out'>()
  for (const column of roles.credits) amounts.set(column, 'in')
  for (const column of roles.debits) amounts.set(column, 'out')
  return amounts.size > 0 ? { balance: roles.balance, date: roles.date ?? -1, amounts } : null
}

/** Characters a column's cells grow to before the reading is cut short on screen. */
const CELL_CHARS: Record<ColumnClass, number> = {
  num: 14,
  'col--text': 20,
  'col--wide': 34,
}

/** The kind's rows under the ticket's own column headers, with its checks above them. */
export function FieldsView({
  result,
  reviewThreshold = REVIEW_THRESHOLD,
  edited = new Set(),
  validated = new Set(),
  onEdit,
  title,
  onResetEdits,
  pages,
  currentPage = 0,
  onEditPage,
  onRemoveRowPage,
  removed = 0,
  onOpenPage,
  statement,
}: FieldsViewProps) {
  const { headers } = toPublicJson(result)
  const pageRowCount = rowCells(result).length
  const ledger = useMemo(() => ledgerOf(statement), [statement])
  const amounts = ledger?.amounts ?? NO_AMOUNTS

  const [query, setQuery] = useState('')
  // The row waiting on an answer: removing it is not something to do by accident.
  const [removing, setRemoving] = useState<{ page: number; row: number } | null>(null)
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
  /*
   * Whether the table takes corrections.
   *
   * A reading is read far more often than it is corrected, and a table of live
   * inputs is a table where a stray click lands in the data. Off by default:
   * the rows are something to check against the page until someone says they
   * are something to change.
   */
  const [editing, setEditing] = useState(false)
  // A long document is read a screenful at a time; the rest is one click away.
  const [expanded, setExpanded] = useState(false)
  const q = query.trim().toLowerCase()

  // A search looks through every page read so far; without one, the table is
  // the page on screen.
  const own = useMemo<PageRows>(
    () => ({ index: currentPage, result, edited, validated }),
    [currentPage, result, edited, validated],
  )
  const allPages = useMemo<readonly PageRows[]>(
    () => (pages && pages.length > 1 ? pages : [own]),
    [pages, own],
  )
  const acrossPages = q.length > 0 && allPages.length > 1
  const scope = useMemo(
    () => (acrossPages ? allPages : [own]).flatMap((page) => rowViews(page, reviewThreshold)),
    [acrossPages, allPages, own, reviewThreshold],
  )

  const { reader, warnings } = result.processingMeta
  const notices = useMemo(
    () => [
      ...(reader.includes('not running')
        ? ['This receipt was read in basic mode, so results may be less accurate. Check every row against the ticket.']
        : []),
      ...new Set(warnings),
    ],
    [reader, warnings],
  )

  const stats = useMemo(
    () => ({
      totalRows: scope.length,
      flaggedCount: scope.filter((row) => row.review).length,
      editedCount: scope.filter((row) => row.edited).length,
      validCount: scope.filter((row) => !row.review).length,
    }),
    [scope],
  )

  const filteredRows = useMemo(
    () =>
      scope.filter((row) => {
        if (filterMode === 'flagged' && !row.review) return false
        if (filterMode === 'edited' && !row.edited) return false
        if (filterMode === 'valid' && row.review) return false
        return !q || row.cells.some((cell) => cell.toLowerCase().includes(q))
      }),
    [scope, filterMode, q],
  )
  const matchedPages = new Set(filteredRows.map((row) => row.page.index)).size
  // Rows past the first screenful wait behind `View More`, unless a filter has
  // already cut the table down to what was asked for.
  const hidden = Math.max(0, filteredRows.length - PAGE_SIZE)
  const shownRows = expanded || hidden === 0 ? filteredRows : filteredRows.slice(0, PAGE_SIZE)

  const edit = (row: RowView, cellIndex: number, value: string) => {
    if (onEditPage) onEditPage(row.page.index, row.index, cellIndex, value)
    else if (row.page.index === currentPage) onEdit?.(row.index, cellIndex, value)
  }
  const offered = Boolean(onEditPage ?? onEdit)
  const editable = offered && editing
  const sameHeaders = (other: OcrResult) => other.headers.join('\u0000') === headers.join('\u0000')

  return (
    <div className="fields-container">
      {/* Compact Slim Warning if issues exist */}
      {notices.length > 0 && (
        <div className="banner-slim" role="alert">
          <svg className="banner-slim__icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <div className="banner-slim__text">
            <strong>{notices.length} warning{notices.length === 1 ? '' : 's'}:</strong>{' '}
            {notices.join(' · ')}
          </div>
        </div>
      )}

      {onRemoveRowPage && (
        <ConfirmDialog
          open={removing !== null}
          title={removing ? `Remove row ${removing.row + 1}${acrossPages ? ` of page ${removing.page + 1}` : ''}?` : ''}
          confirmLabel="Remove row"
          onCancel={() => setRemoving(null)}
          onConfirm={() => {
            if (removing) onRemoveRowPage(removing.page, removing.row)
            setRemoving(null)
          }}
        >
          <p>It leaves the table and every export. Reset edits &amp; rows brings back the rows removed from this page.</p>
        </ConfirmDialog>
      )}

      {/* Search on the left, ticket title on the right. */}
      <div className="table-controls">
        <div className="table-controls__start">
          <div className="search-box">
          <svg className="search-box__icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            role="searchbox"
            autoComplete="off"
            spellCheck={false}
            className="search-box__input"
            placeholder={allPages.length > 1 ? `Search all ${allPages.length} pages…` : 'Search extracted data…'}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={allPages.length > 1 ? 'Search rows on every page' : 'Filter rows'}
          />
          {query && (
            <button
              type="button"
              className="search-box__clear"
              onClick={() => setQuery('')}
              aria-label="Clear filter"
              title="Clear search"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          )}
          </div>

          {/* One picker rather than a tab each: the counts are above the
              table already, and a row of tabs crowds out the search. */}
          <div className="field-select">
            <select
              className="field-select__input"
              value={filterMode}
              aria-label="Which rows to show"
              onChange={(event) => {
                setFilterMode(event.target.value as FilterMode)
                setExpanded(false)
              }}
            >
              {FILTERS.map(({ value, label }) => (
                <option key={value} value={value}>
                  {label} ({value === 'all'
                    ? stats.totalRows
                    : value === 'flagged'
                      ? stats.flaggedCount
                      : value === 'edited'
                        ? stats.editedCount
                        : stats.validCount})
                </option>
              ))}
            </select>
            <svg className="field-select__caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </div>
        </div>

        <div className="table-controls__end">
          <span className="kind-badge">{title}</span>
          <span className="count">
            {pageRowCount} rows
            {edited.size > 0 && ` · ${edited.size} edited`}
            {removed > 0 && ` · ${removed} removed`}
          </span>
          {offered && (
            <button
              type="button"
              role="switch"
              aria-checked={editing}
              className={`switch${editing ? ' switch--on' : ''}`}
              onClick={() => setEditing((on) => !on)}
              title={
                editing
                  ? 'Leave the rows as they are read'
                  : 'Correct a reading by typing into the table'
              }
            >
              <span className="switch__track" aria-hidden="true">
                <span className="switch__knob" />
              </span>
              Edit
            </button>
          )}
          {(edited.size > 0 || removed > 0) && onResetEdits && (
            <button type="button" className="btn btn--sm btn--subtle" onClick={onResetEdits}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                <path d="M3 3v5h5" />
              </svg>
              {removed > 0 ? 'Reset edits & rows' : 'Reset edits'}
            </button>
          )}
        </div>
      </div>

      {acrossPages && filteredRows.length > 0 && (
        <p className="search-scope" aria-live="polite">
          {filteredRows.length} row{filteredRows.length === 1 ? '' : 's'} on {matchedPages} of{' '}
          {allPages.length} pages match "{query.trim()}"
        </p>
      )}

      {scope.length === 0 ? (
        <p className="column__empty">{EMPTY}</p>
      ) : filteredRows.length === 0 ? (
        <div className="empty-filter-state">
          <p>
            No rows {acrossPages ? `on any of the ${allPages.length} pages ` : ''}match "{query}".
          </p>
          <button type="button" className="btn btn--sm" onClick={() => { setQuery(''); setFilterMode('all'); }}>
            Reset Filter
          </button>
        </div>
      ) : (
        <div className="table-responsive">
          <table className={`fields${editable ? ' fields--editing' : ''}`}>
            <thead>
              <tr>
                {acrossPages && <th className="num th--seq th--page">Pg</th>}
                <th className="num th--seq">#</th>
                {headers.map((header, index) => (
                  <th
                    key={`${header}-${index}`}
                    className={`${columnClass(result, index)}${ledger?.balance === index ? ' th--balance' : ''}`}
                  >
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shownRows.map((row, position) => {
                const { page, index: originalIndex, confidence, label } = row
                const own = page.result
                const ownHeaders = own.headers
                const complete = row.cells.every((cell) => cell.trim() !== '')
                // A statement's row says which way the money went by filling one
                // amount column and leaving the other blank. A row with neither
                // filled says nothing, and that is worth seeing.
                const hasAmount = row.cells.some(
                  (cell, index) => amounts.has(index) && cell.trim() !== '',
                )
                const tone =
                  row.edited && complete
                    ? 'fields__row--edited'
                    : row.flagged
                      ? 'fields__row--flagged'
                      : confidence < reviewThreshold
                        ? 'fields__row--low'
                        : ''
                const total = row.total ? 'fields__row--total' : ''
                const className =
                  [tone, total, label ? 'fields__row--label' : ''].filter(Boolean).join(' ') ||
                  undefined
                // A page set out with other columns shows its own titles above its rows.
                const previous = shownRows[position - 1]
                const retitle =
                  acrossPages && previous?.page.index !== page.index && !sameHeaders(own)

                return (
                  <Fragment key={`${page.index}:${originalIndex}`}>
                    {retitle && (
                      <tr className="fields__retitle">
                        <td className="td--seq td--page" />
                        <td className="td--seq" />
                        {ownHeaders.map((header, index) => (
                          <td key={`${header}-${index}`} className={columnClass(own, index)}>
                            {header}
                          </td>
                        ))}
                      </tr>
                    )}
                    <tr className={className}>
                      {acrossPages && (
                        <td className="num td--seq td--page">
                          {onOpenPage ? (
                            <button
                              type="button"
                              className="page-link"
                              onClick={() => onOpenPage(page.index)}
                              title={`Show page ${page.index + 1}`}
                              aria-label={`Show page ${page.index + 1}`}
                            >
                              {page.index + 1}
                            </button>
                          ) : (
                            page.index + 1
                          )}
                        </td>
                      )}
                      <td className="num td--seq" title={`Row ${originalIndex + 1}`}>
                        <span className="seq__number">{originalIndex + 1}</span>
                        {onRemoveRowPage && editable && (
                          <button
                            type="button"
                            className="seq__remove"
                            onClick={() => setRemoving({ page: page.index, row: originalIndex })}
                            title={`Remove row ${originalIndex + 1}`}
                            aria-label={
                              acrossPages
                                ? `Remove row ${originalIndex + 1} of page ${page.index + 1}`
                                : `Remove row ${originalIndex + 1}`
                            }
                          >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                              <polyline points="3 6 5 6 21 6" />
                              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                              <path d="M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                            </svg>
                          </button>
                        )}
                      </td>
                      {row.cells.map((cell, cellIndex) => {
                        const header = ownHeaders[cellIndex] ?? ''
                        const column = columnClass(own, cellIndex)
                        const part = amounts.get(cellIndex)
                        /*
                         * What a blank means on a statement, column by column.
                         *
                         * A transaction is paid in or paid out and not both, and
                         * a wire transfer has no check number: most of a ledger's
                         * blanks are the page as printed, and flagging them would
                         * put the colour of a missing reading on half the cells of
                         * every page. The blanks that are something missing are
                         * the ones the ledger is made of — the date, the running
                         * balance, and an amount on a row that carries none.
                         */
                        const needed =
                          cellIndex === ledger?.balance ||
                          cellIndex === ledger?.date ||
                          (part !== undefined && !hasAmount)
                        const blankOnThePage = ledger !== null && !needed
                        // A label's other cells are blank on the page, not missing.
                        const isEmpty = cell.trim() === '' && !label && !blankOnThePage
                        const isLowConfidence = confidence < reviewThreshold
                        // Cells ask for the width of their reading, up to the column's cap,
                        // so one long description cannot stretch the table past the card.
                        const width = Math.min(Math.max(cell.length, header.length, 4), CELL_CHARS[column])

                        return (
                          <td
                            key={`${header}-${cellIndex}`}
                            className={`${column}${ledger?.balance === cellIndex ? ' td--balance' : ''}`}
                            title={row.edited ? 'Edited manually' : `Confidence: ${(confidence * 100).toFixed(0)}%`}
                          >
                            <input
                              className={`cell-input${isEmpty ? ' cell-input--empty' : ''}${isLowConfidence ? ' cell-input--low' : ''}${part && cell.trim() ? ` cell-input--${part}` : ''}`}
                              value={cell}
                              size={width}
                              // A reading too long for its column is cut short: hover shows all of it.
                              title={cell.length > width ? cell : undefined}
                              placeholder={label ? '' : blankOnThePage ? '—' : 'empty'}
                              aria-label={
                                acrossPages
                                  ? `${header}, page ${page.index + 1}, row ${originalIndex + 1}`
                                  : `${header}, row ${originalIndex + 1}`
                              }
                              readOnly={!editable}
                              spellCheck={false}
                              // Read-only stops a person typing; this stops
                              // anything else, so the switch is the one way in.
                              onChange={(event) =>
                                editable && edit(row, cellIndex, event.target.value)
                              }
                            />
                          </td>
                        )
                      })}
                    </tr>
                  </Fragment>
                )
              })}
            </tbody>
          </table>
          {hidden > 0 && (
            <div className="view-more">
              <button
                type="button"
                className="btn btn--sm btn--subtle view-more__btn"
                onClick={() => setExpanded((open) => !open)}
                aria-expanded={expanded}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <polyline points={expanded ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
                </svg>
                {expanded ? 'View Less' : `View More (${hidden})`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Tables printed under the main one                                          */
/* -------------------------------------------------------------------------- */

interface ExtraTablesProps {
  /** Every page read so far, in page order. */
  pages: readonly ExportPage[]
  /** The tables printed under the pages' own, as the reader gathered them. */
  tables: readonly ExtraTable[]
  /** Pages in the document: 1 for an image. */
  total: number
  /** Index of the page on screen within the document. */
  currentPage: number
  /** The uploaded file's name, for the downloads. */
  fileName: string | null
  /** Tables left out of the JSON export, by `ExtraTable.key`. */
  dropped: ReadonlySet<string>
  /** Take a table out of the JSON export, or put it back. */
  onToggleTable: (key: string) => void
}

/**
 * The tables the page on screen prints under its own: an invoice's
 * `Previous Balances`, a recap of the order by category.
 *
 * Shown for that page and in the order it prints them, beside the rows of
 * the same page: a table printed on page 3 is not something page 2 has. The
 * download is the whole document's, since a table repeated on every page is
 * one table to whoever reads the file.
 */
export function ExtraTables({
  pages,
  tables,
  total,
  currentPage,
  fileName,
  dropped,
  onToggleTable,
}: ExtraTablesProps) {
  const page = currentPage + 1
  // The page's own table — its first list, which the primary table already
  // shows — is left out here so a statement's Other Debits is not drawn twice
  // on a page that is its own and a second list's continuation both.
  const ownTitle = pages.find((each) => each.page === page)?.result.title
  const onPage = useMemo(
    () =>
      tables
        .filter((table) => table.title !== ownTitle)
        .map((table) => ({ table, rows: table.rows.filter((row) => row.page === page) }))
        .filter(({ rows }) => rows.length > 0),
    [tables, page, ownTitle],
  )
  if (onPage.length === 0) return null
  const base = exportBaseName(fileName, 'receipt')

  return (
    <>
      {onPage.map(({ table, rows }) => {
        const out = dropped.has(table.key)
        const classes = tableColumnClasses(table.headers, rows.map((row) => row.cells))
        return (
        <div className={`card${out ? ' card--dropped' : ''}`} key={table.key}>
          <div className="card__head">
            <div className="export-title-group">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <line x1="3" y1="9" x2="21" y2="9" />
                <line x1="9" y1="9" x2="9" y2="21" />
              </svg>
              <h2 className="card__title">{table.title}</h2>
              <span className="count">
                {rows.length} {rows.length === 1 ? 'row' : 'rows'}
                {/* The same table printed on other pages too: the download
                    carries all of them, so say how many. */}
                {table.rows.length > rows.length ? ` · ${table.rows.length} in all` : ''}
              </span>
              {out && (
                <span className="dropped-chip" title="This table is left out of the JSON export">
                  Not in JSON
                </span>
              )}
            </div>
            <div className="btn-row">
              {/* The downloads are this table on its own, and stay whatever
                  the export carries: taking a table out of the document is
                  not a reason to stop being able to save it. */}
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => {
                  void requestExport({
                    what: 'table',
                    format: 'csv',
                    key: table.key,
                    pages,
                    total,
                  }).then((text) =>
                    // The byte-order mark tells Excel the file is UTF-8.
                    saveFile(`﻿${text}`, 'text/csv;charset=utf-8;', `${base}-${table.slug}.csv`),
                  )
                }}
                title={
                  table.rows.length > rows.length
                    ? `Download ${table.title} from every page as a CSV spreadsheet`
                    : `Download ${table.title} as a CSV spreadsheet`
                }
              >
                <DownloadIcon />
                CSV
              </button>
              <button
                type="button"
                className="btn btn--sm"
                onClick={() => {
                  void requestExport({
                    what: 'table',
                    format: 'json',
                    key: table.key,
                    pages,
                    total,
                  }).then((text) =>
                    saveFile(`${text}\n`, 'application/json', `${base}-${table.slug}.json`),
                  )
                }}
                title={
                  table.rows.length > rows.length
                    ? `Download ${table.title} from every page as JSON`
                    : `Download ${table.title} as JSON`
                }
              >
                <DownloadIcon />
                JSON
              </button>
              <button
                type="button"
                className={`btn btn--sm${out ? '' : ' btn--subtle'}`}
                onClick={() => onToggleTable(table.key)}
                aria-pressed={out}
                title={
                  out
                    ? `Put ${table.title} back into the JSON export`
                    : `Leave ${table.title} out of the JSON export`
                }
              >
                {out ? (
                  <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <polyline points="1 4 1 10 7 10" />
                      <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
                    </svg>
                    Restore
                  </>
                ) : (
                  <>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                    Remove
                  </>
                )}
              </button>
            </div>
          </div>
          <div className="card__body card__body--compact">
            <div className="table-responsive">
              <table className="fields fields--static">
                <thead>
                  <tr>
                    {table.headers.map((header, index) => (
                      <th key={`${header}-${index}`} className={classes[index] ?? 'col--text'}>
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={index}>
                      {table.headers.map((header, cell) => (
                        <td
                          key={`${header}-${cell}`}
                          className={classes[cell] ?? 'col--text'}
                          title={(row.cells[cell] ?? '').length > CELL_CHARS[classes[cell] ?? 'col--text'] ? row.cells[cell] : undefined}
                        >
                          {row.cells[cell] ?? ''}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        )
      })}
    </>
  )
}
