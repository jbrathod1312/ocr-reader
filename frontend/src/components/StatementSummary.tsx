import type { StatementSummary as Summary } from '../ocr/api'

/**
 * What the statement prints about itself, above the rows it was read into.
 *
 * Every figure here is one the page carries. Nothing is worked out, and nothing
 * is lifted out of a row — the whole point of the reading is that what is on
 * screen is what is on the paper.
 *
 * Two kinds of statement prove themselves two ways, so this shows two things.
 * A `ledger` statement prints a running balance beside every row, and the check
 * is that each balance follows from the one before it. A `sectioned` statement
 * prints no running balance; it states a previous and an ending balance in a
 * box at its head, with the month's additions and subtractions, and the check
 * is that the box adds up to itself and that the lists of transactions add up to
 * the box. The summary shows whichever checks the statement in hand allows.
 *
 * Under them are the fields the page prints at its head — an account number, the
 * balances it states for itself — copied as printed, label and value.
 */
export function StatementSummary({ statement }: { statement: Summary }) {
  const checks =
    statement.kind === 'sectioned' && statement.box
      ? sectionedChecks(statement, statement.box)
      : ledgerChecks(statement)

  // Earliest to latest, whichever end of the month the statement starts at.
  const dates =
    statement.firstDate && statement.lastDate
      ? statement.newestFirst === false
        ? [statement.firstDate, statement.lastDate]
        : [statement.lastDate, statement.firstDate]
      : null

  return (
    <div className="statement" aria-label="Statement summary">
      {statement.bank && <div className="statement__bank">{statement.bank}</div>}
      {checks.facts.some(({ value }) => value !== null) && (
        <dl className="statement__facts">
          {checks.facts.map(
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
        {checks.chips.map((chip, index) => (
          <span key={index} className={`statement__chip statement__chip--${chip.tone}`}>
            {chip.text}
          </span>
        ))}
      </div>
      <p className="statement__period">
        {checks.count}
        {dates && ` · ${dates[0]} – ${dates[1]}`}
      </p>
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

interface Chip {
  tone: 'ok' | 'bad' | 'muted'
  text: string
}

interface Fact {
  label: string
  /** As printed, or null where the statement prints no such figure. */
  value: string | null
  tone: 'in' | 'out' | 'neutral'
  note: string
}

interface Checks {
  facts: Fact[]
  chips: Chip[]
  /** The quietest line: how many rows, and of what. */
  count: string
}

/** Where a figure is printed, so hovering one says where it was read from. */
const PRINTED_TOTAL = 'As printed under the rows of the statement.'
const PRINTED_BOX = 'As printed in the statement summary.'

/**
 * A ledger statement: its debits and credits, and whether the running balance
 * follows row to row. A balance belongs to the row it is printed on, so none is
 * lifted up here — the column is in the table, where each belongs to its row.
 */
function ledgerChecks(statement: Summary): Checks {
  const { balanceCheck, balanceLinksChecked, balanceLinksBroken, stated } = statement
  const totalsOk =
    (stated.debits === null || stated.debits === statement.debits) &&
    (stated.credits === null || stated.credits === statement.credits)
  const countOk = stated.transactions === null || stated.transactions === statement.transactions

  const chips: Chip[] = [
    balanceCheck === 'ok'
      ? { tone: 'ok', text: `Balance checks out · ${balanceLinksChecked} rows follow each other` }
      : balanceCheck === 'broken'
        ? {
            tone: 'bad',
            text: `Balance breaks ${balanceLinksBroken} time${balanceLinksBroken === 1 ? '' : 's'} in ${balanceLinksChecked} rows`,
          }
        : { tone: 'muted', text: 'Balance not checked · too few rows with a balance' },
  ]
  if (stated.transactions !== null)
    chips.push({
      tone: countOk ? 'ok' : 'bad',
      text: countOk
        ? `All ${stated.transactions} transactions read`
        : `Statement says ${stated.transactions}, read ${statement.transactions}`,
    })
  if (stated.debits !== null || stated.credits !== null)
    chips.push({
      tone: totalsOk ? 'ok' : 'bad',
      text: totalsOk ? 'Totals match the statement' : 'Totals differ from the statement',
    })

  return {
    facts: [
      { label: 'Debits', value: stated.debits, tone: 'out', note: PRINTED_TOTAL },
      { label: 'Credits', value: stated.credits, tone: 'in', note: PRINTED_TOTAL },
    ],
    chips,
    count: `${statement.transactions} row${statement.transactions === 1 ? '' : 's'} read`,
  }
}

/**
 * A sectioned statement: the four figures its box balances by, and whether the
 * arithmetic holds — the box against itself, and the lists against the box.
 */
function sectionedChecks(statement: Summary, box: NonNullable<Summary['box']>): Checks {
  const chips: Chip[] = [
    box.previous !== null && box.ending !== null
      ? box.balances
        ? { tone: 'ok', text: 'Balance adds up · opening + credits − debits = closing' }
        : { tone: 'bad', text: 'Balance does not add up' }
      : { tone: 'muted', text: 'Balance not checked · the summary was not fully read' },
  ]
  if (box.additions !== null || box.subtractions !== null) {
    const listsMatch = box.additionsMatch && box.subtractionsMatch
    chips.push({
      tone: listsMatch ? 'ok' : 'bad',
      text: listsMatch ? 'Lists match the statement totals' : 'Lists differ from the statement totals',
    })
  }

  return {
    facts: [
      { label: 'Opening', value: box.previous, tone: 'neutral', note: PRINTED_BOX },
      { label: 'Credits', value: box.additions, tone: 'in', note: PRINTED_BOX },
      { label: 'Debits', value: box.subtractions, tone: 'out', note: PRINTED_BOX },
      { label: 'Closing', value: box.ending, tone: 'neutral', note: PRINTED_BOX },
    ],
    chips,
    count: sectionedCount(statement),
  }
}

/** `167 transactions · 64 checks, 67 debits, 36 credits`, from the section counts. */
function sectionedCount(statement: Summary): string {
  const total = `${statement.transactions} transaction${statement.transactions === 1 ? '' : 's'}`
  const parts = Object.entries(statement.counts ?? {})
    .filter(([, n]) => n > 0)
    .map(([name, n]) => `${n} ${name.toLowerCase()}`)
  return parts.length > 1 ? `${total} · ${parts.join(', ')}` : total
}
