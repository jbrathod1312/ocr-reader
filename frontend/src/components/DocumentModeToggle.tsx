import type { DocumentMode } from '../ocr/api'

const MODES: { value: DocumentMode; label: string; hint: string }[] = [
  { value: 'receipt', label: 'Receipt / invoice', hint: 'Invoices, orders, receipts and any other table' },
  { value: 'lottery', label: 'Lottery', hint: 'Instant inventory, pack settlements and the weekly invoice' },
  { value: 'bank', label: 'Bank statement', hint: 'Dates, debits and credits, checked against the running balance' },
]

interface DocumentModeToggleProps {
  mode: DocumentMode
  onChange: (mode: DocumentMode) => void
  disabled?: boolean
}

/**
 * What the document is, said by the person who knows.
 *
 * The reader does not work this out. The choice picks which reader runs (the
 * general table reader, the lottery's, or the bank statement's), and
 * changing it with a file loaded reads that file again as the new kind.
 */
export function DocumentModeToggle({ mode, onChange, disabled = false }: DocumentModeToggleProps) {
  return (
    <div className="mode-toggle">
      <span className="mode-toggle__label" id="mode-toggle-label">
        Document type
      </span>
      <div className="segmented" role="group" aria-labelledby="mode-toggle-label">
        {MODES.map(({ value, label, hint }) => (
          <button
            key={value}
            type="button"
            className={`segmented__option${mode === value ? ' segmented__option--active' : ''}`}
            aria-pressed={mode === value}
            title={hint}
            disabled={disabled}
            onClick={() => onChange(value)}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}
