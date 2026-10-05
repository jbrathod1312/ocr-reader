import type { StatementSummary as Summary } from '../ocr/api'

/**
 * What the statement says about itself, above the rows it was read into.
 *
 * Its running balance is a proof: row by row, each balance has to follow from
 * the one beside it. Where it does, the reading is right; where it breaks, the
 * rows named in the warnings are the ones to look at.
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

  const facts: [string, string | null][] = [
    ['Transactions', String(statement.transactions)],
    ['Opening', statement.openingBalance],
    ['Closing', statement.closingBalance],
    ['Debits', statement.debits],
    ['Credits', statement.credits],
  ]

  return (
    <div className="statement" aria-label="Statement summary">
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
      </div>
      <dl className="statement__facts">
        {facts.map(
          ([label, value]) =>
            value !== null && (
              <div key={label} className="statement__fact">
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ),
        )}
        {statement.firstDate && statement.lastDate && (
          <div className="statement__fact">
            <dt>Dates</dt>
            <dd>
              {statement.lastDate} – {statement.firstDate}
            </dd>
          </div>
        )}
      </dl>
    </div>
  )
}
