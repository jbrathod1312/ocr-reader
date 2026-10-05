import { useEffect, useRef, type ReactNode } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
}

/**
 * A question the page must have answered before it goes on.
 *
 * The browser's own modal dialog, so focus stays inside it, Escape cancels,
 * and nothing behind it can be clicked. Focus starts on Cancel: a held Enter
 * or a stray click should not be what removes a row.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className="confirm"
      aria-labelledby="confirm-title"
      onCancel={(event) => {
        // Escape: let the state close it, so the two never disagree.
        event.preventDefault()
        onCancel()
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog itself.
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      {open && (
        <div className="confirm__body">
          <h3 id="confirm-title" className="confirm__title">
            {title}
          </h3>
          <div className="confirm__text">{children}</div>
          <div className="confirm__actions">
            <button type="button" className="btn btn--sm btn--subtle" onClick={onCancel} autoFocus>
              Cancel
            </button>
            <button type="button" className="btn btn--sm btn--alert" onClick={onConfirm}>
              {confirmLabel}
            </button>
          </div>
        </div>
      )}
    </dialog>
  )
}
