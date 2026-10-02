/** "Are you sure?" before an action that cannot be undone.
 *
 * <dialog>, as the drawer and the account: focus trapping, Escape and
 * inertness for free. window.confirm would do the job in one line, but it
 * renders in the browser's chrome, cannot say which button is the dangerous
 * one, and some embedded browsers silently answer it "no".
 */

import { useEffect, useRef, type ReactNode } from 'react'
import './confirm.css'

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    // jsdom has no showModal; the dialog is then simply not shown modally.
    if (open && !dialog.open) dialog.showModal?.()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className="confirm"
      aria-labelledby="confirm-title"
      onCancel={(event) => {
        event.preventDefault()
        onCancel()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onCancel()
      }}
    >
      <h2 id="confirm-title" className="confirm__title">
        {title}
      </h2>
      <div className="confirm__body">{children}</div>
      <div className="confirm__actions">
        {/* Focus lands on the safe choice: Enter on a dialog that just
            opened should not delete anything. */}
        <button type="button" className="btn btn--ghost" onClick={onCancel} autoFocus>
          Отмена
        </button>
        <button type="button" className="btn confirm__danger" onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  )
}
