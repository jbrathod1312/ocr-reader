import type { StatementSummary as Summary } from '../ocr/api'

/**
 * What the statement prints about itself, above the rows it was read into.
 *
 * Every figure here is one the page carries: the totals printed under its rows.
 * Nothing is worked out, and nothing is lifted out of a row — a balance belongs
 * to the row it is printed on, and the table is where the rows are. A statement
 * that prints no totals shows none, because a figure the reader arrived at
 * would be read as one the statement made, and the whole point of the reading
 * is that what is on screen is what is on the paper.
 *
 * Under them are the fields the page prints beside its table — an account
 * number, the balances it states for itself — copied as printed, label and
 * value. Which is which is punctuation and position, so this shows whatever a
 * statement happens to print there rather than a set of fields chosen in
 * advance, and makes nothing of what any of them means.
 *
 * The checks are the other half, and they are about the rows rather than any
 * figure of their own: each balance has to follow from the one beside it, and
 * the rows have to add up to what the statement says they do. Where that holds
 * the reading is right; where it breaks, the warnings name the rows to look at.
 */
export function StatementSummary({ statement }: { statement: Summary }) {
  const { balanceCheck, balanceLinksChecked, balanceLinksBroken, stated } = statement
  const countOk = stated.transactions === null || stated.transactions === statement.transactions
  const totalsOk =
    (stated.debits === null || stated.debits === statement.debits) &&
    (stated.credits === null || stated.credits === statement.credits)

  const balance =
    balanceCheck === 'ok'
      ? { tone: 'ok', text: `Balance checks out · ${balanceLinksChecked} rows follow each other` }
      : balanceCheck === 'broken'
        ? {
            tone: 'bad',
            text: `Balance breaks ${balanceLinksBroken} time${balanceLinksBroken === 1 ? '' : 's'} in ${balanceLinksChecked} rows`,
          }
        : { tone: 'muted', text: 'Balance not checked · too few rows with a balance' }

  // Earliest to latest, whichever end of the month the statement starts at.
  const dates =
    statement.firstDate && statement.lastDate
      ? statement.newestFirst === false
        ? [statement.firstDate, statement.lastDate]
        : [statement.lastDate, statement.firstDate]
      : null

  // A statement has a balance per row and not one of its own, so no balance is
  // lifted up here: the column is in the table, where each belongs to its row.
  const facts: Fact[] = [
    { label: 'Debits', value: stated.debits, tone: 'out', note: PRINTED_TOTAL },
    { label: 'Credits', value: stated.credits, tone: 'in', note: PRINTED_TOTAL },
  ]

  return (
    <div className="statement" aria-label="Statement summary">
      {facts.some(({ value }) => value !== null) && (
        <dl className="statement__facts">
          {facts.map(
            ({ label, value, tone, note }) =>
              value !== null && (
                <div key={label} className={`statement__fact statement__fact--${tone}`} title={note}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ),
          )}
        </dl>
      )}
      <div className="statement__checks">
        <span className={`statement__chip statement__chip--${balance.tone}`}>{balance.text}</span>
        {stated.transactions !== null && (
          <span className={`statement__chip statement__chip--${countOk ? 'ok' : 'bad'}`}>
            {countOk
              ? `All ${stated.transactions} transactions read`
              : `Statement says ${stated.transactions}, read ${statement.transactions}`}
          </span>
        )}
        {(stated.debits !== null || stated.credits !== null) && (
          <span className={`statement__chip statement__chip--${totalsOk ? 'ok' : 'bad'}`}>
            {totalsOk ? 'Totals match the statement' : 'Totals differ from the statement'}
          </span>
        )}
        <span className="statement__period">
          {statement.transactions} row{statement.transactions === 1 ? '' : 's'} read
          {dates && ` · ${dates[0]} – ${dates[1]}`}
        </span>
      </div>
      {statement.details.length > 0 && (
        <dl className="statement__details">
          {statement.details.map(({ label, value, page }) => (
            <div
              key={`${label}\u0000${value}`}
              className="statement__detail"
              title={`Printed on page ${page}`}
            >
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

/** Where the figure is printed, so hovering one says where it was read from. */
const PRINTED_TOTAL = 'As printed under the rows of the statement.'

/** One figure the statement prints, and what it is. */
interface Fact {
  label: string
  /** As printed, or null where the statement prints no such figure. */
  value: string | null
  tone: 'in' | 'out'
  note: string
}
