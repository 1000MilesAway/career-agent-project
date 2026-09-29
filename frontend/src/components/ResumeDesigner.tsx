/** The resume designer: pick a style, see the sheet, download it.
 *
 * The preview is the server's own HTML — the page WeasyPrint turns into the
 * PDF — shown in an iframe. Drawing the preview in React would be a second
 * implementation of every style, and the two would drift until the preview
 * promised a look the file does not have.
 *
 * The iframe is sandboxed without `allow-scripts`, so nothing in it can run.
 * `allow-same-origin` is granted only so this component can read the page's
 * height: without it the frame is cross-origin, its height unknowable, and a
 * two-page CV would be cut off after the first sheet. With scripts off, the
 * origin gives the frame nothing to act with.
 */

import { useEffect, useRef, useState } from 'react'
import { type ExportFormat, useResumePreview, useResumeStyles } from '../api/hooks'
import { CloseIcon } from './icons'
import { useToast } from './toast'
import './designer.css'

// A4 at 96 dpi: the width the preview page lays itself out at.
const SHEET_PX = 794
const SHEET_HEIGHT_PX = 1123

const FORMATS: { fmt: ExportFormat; label: string }[] = [
  { fmt: 'pdf', label: 'Скачать PDF' },
  { fmt: 'docx', label: 'DOCX' },
]

export function ResumeDesigner({
  open,
  title,
  exportBase,
  style,
  onStyleChange,
  onDownload,
  onClose,
}: {
  open: boolean
  title: string
  exportBase: string
  style: string
  onStyleChange: (style: string) => void
  onDownload: (fmt: ExportFormat, style: string) => Promise<void>
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const { data: styles } = useResumeStyles(open)
  const preview = useResumePreview(open ? exportBase : null, style)
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const toast = useToast()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  // The sheet is laid out at A4 width and scaled to fit the pane, so a
  // narrow screen shows the whole page rather than its left half.
  const paneRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [sheetHeight, setSheetHeight] = useState(SHEET_HEIGHT_PX)
  useEffect(() => {
    const pane = paneRef.current
    if (!pane || !open) return
    const observer = new ResizeObserver(([entry]) => {
      setScale(Math.min(1, entry.contentRect.width / SHEET_PX))
    })
    observer.observe(pane)
    return () => observer.disconnect()
  }, [open])

  async function download(fmt: ExportFormat) {
    setBusy(fmt)
    try {
      await onDownload(fmt, style)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <dialog
      ref={ref}
      className="designer"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <header className="designer__head">
        <h2 className="designer__title">{title}</h2>
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

      <div className="designer__body">
        <aside className="designer__side">
          <fieldset className="designer__styles">
            <legend className="designer__legend">Оформление</legend>
            {styles ? (
              styles.map((s) => (
                <label
                  key={s.id}
                  className={`designer__style${s.id === style ? ' designer__style--on' : ''}`}
                >
                  <input
                    type="radio"
                    name="resume-style"
                    value={s.id}
                    checked={s.id === style}
                    onChange={() => onStyleChange(s.id)}
                  />
                  <span className="designer__style-name">{s.name}</span>
                  <span className="designer__style-desc muted">{s.description}</span>
                </label>
              ))
            ) : (
              <p className="muted">Загружаю стили…</p>
            )}
          </fieldset>

          <div className="designer__actions">
            {FORMATS.map(({ fmt, label }) => (
              <button
                key={fmt}
                className={fmt === 'pdf' ? 'btn' : 'btn btn--ghost'}
                onClick={() => download(fmt)}
                disabled={busy !== null}
              >
                {busy === fmt ? 'Готовлю…' : label}
              </button>
            ))}
          </div>
          <p className="muted designer__note">
            Текст одинаков во всех стилях — меняется только вид. Бесплатно.
          </p>
        </aside>

        <div className="designer__pane" ref={paneRef}>
          {preview.error ? (
            <p className="designer__error">
              Превью не загрузилось: {String(preview.error.message)}
            </p>
          ) : preview.data ? (
            <div
              className="designer__sheet"
              style={{ width: SHEET_PX * scale, height: sheetHeight * scale }}
            >
              <iframe
                title="Превью резюме"
                sandbox="allow-same-origin"
                srcDoc={preview.data}
                onLoad={(event) => {
                  const page = event.currentTarget.contentDocument?.documentElement
                  if (page) setSheetHeight(Math.max(SHEET_HEIGHT_PX, page.scrollHeight))
                }}
                className={
                  preview.isValidating ? 'designer__frame is-loading' : 'designer__frame'
                }
                style={{
                  width: SHEET_PX,
                  height: sheetHeight,
                  transform: `scale(${scale})`,
                }}
              />
            </div>
          ) : (
            <p className="muted">Готовлю превью…</p>
          )}
        </div>
      </div>
    </dialog>
  )
}
