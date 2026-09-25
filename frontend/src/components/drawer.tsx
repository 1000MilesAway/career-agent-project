/** A side panel for a report or a document.
 *
 * The old page appended the report to the bottom of the document and scrolled
 * there, which lost the list: closing the report meant scrolling back up and
 * finding the row again. A panel keeps the list in place underneath.
 *
 * <dialog> rather than a hand-rolled overlay — the browser gives focus
 * trapping, Escape and inertness for free, and all three were missing before.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { CloseIcon } from './icons'
import './drawer.css'

export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  // Escape fires `cancel`, and the backdrop click lands on the dialog element
  // itself; both have to reach the caller or the panel closes visually while
  // the caller still thinks it is open.
  return (
    <dialog
      ref={ref}
      className="drawer"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <header className="drawer__head">
        <h2 className="drawer__title">{title}</h2>
        <button
          type="button"
          className="btn btn--quiet btn--small btn--icon"
          onClick={onClose}
          aria-label="Закрыть"
          title="Закрыть (Esc)"
        >
          <CloseIcon />
        </button>
      </header>
      <div className="drawer__body">{children}</div>
    </dialog>
  )
}
