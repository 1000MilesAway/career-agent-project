/** "Скачать ▾" with PDF, DOCX and MD under it — used on both screens.
 *
 * Shared so the two places cannot disagree on what a download costs. Nothing
 * here is paid: the server renders JSON that already exists.
 *
 * PDF opens the designer: it is the format people send, so the look is chosen
 * on a preview before the file is made. DOCX and MD download at once, in the
 * style last picked there, so a user who chose "Акцент" once does not have to
 * open the designer again for every vacancy.
 */

import { useState, type FocusEvent } from 'react'
import { downloadExport, type ExportFormat, useResumeStyles } from '../api/hooks'
import { ResumeDesigner } from './ResumeDesigner'
import { DEFAULT_STYLE, useResumeStyleChoice } from './resumeStyleChoice'
import { useToast } from './toast'

const LABELS: Record<ExportFormat, string> = { pdf: 'PDF', docx: 'DOCX', md: 'MD' }

export function DownloadButtons({
  exportBase,
  small = false,
  label = 'Скачать',
  designerTitle = 'Оформление резюме',
}: {
  /** The export route without the format, e.g. `/api/export/resume`. */
  exportBase: string
  small?: boolean
  /** Screen-reader context: two identical "Скачать" buttons on one page otherwise. */
  label?: string
  designerTitle?: string
}) {
  const [busy, setBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [designerOpen, setDesignerOpen] = useState(false)
  const [chosen, choose] = useResumeStyleChoice()
  const { data: styles } = useResumeStyles()
  const toast = useToast()

  // A remembered style the server no longer offers would answer 422 on every
  // download; fall back to the default instead of breaking the buttons.
  const style = !styles || styles.some((s) => s.id === chosen) ? chosen : DEFAULT_STYLE

  async function run(fmt: ExportFormat) {
    setMenuOpen(false)
    setBusy(true)
    try {
      await downloadExport(exportBase, fmt, style)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(false)
    }
  }

  function openDesigner() {
    setMenuOpen(false)
    setDesignerOpen(true)
  }

  // Focus leaving the whole group closes the menu: a click elsewhere, or Tab
  // past the last item. A dropdown left open over the list is in the way.
  function onBlur(event: FocusEvent<HTMLSpanElement>) {
    if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false)
  }

  const className = small ? 'btn btn--ghost btn--small' : 'btn btn--ghost'
  const styleName = styles?.find((s) => s.id === style)?.name

  return (
    <>
      <span
        className="download"
        onBlur={onBlur}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setMenuOpen(false)
        }}
      >
        <button
          className={className}
          onClick={() => setMenuOpen(!menuOpen)}
          disabled={busy}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={label}
        >
          {busy ? 'Готовлю…' : 'Скачать ▾'}
        </button>
        {menuOpen ? (
          <span className="download__menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="download__item"
              onClick={openDesigner}
              aria-label={`${label} — ${LABELS.pdf}`}
              title={
                styleName
                  ? `Оформление: ${styleName}. Выбрать перед скачиванием`
                  : undefined
              }
            >
              {LABELS.pdf}
            </button>
            {(['docx', 'md'] as const).map((fmt) => (
              <button
                key={fmt}
                type="button"
                role="menuitem"
                className="download__item"
                onClick={() => run(fmt)}
                aria-label={`${label} — ${LABELS[fmt]}`}
              >
                {LABELS[fmt]}
              </button>
            ))}
          </span>
        ) : null}
      </span>
      {designerOpen ? (
        <ResumeDesigner
          open
          title={designerTitle}
          exportBase={exportBase}
          style={style}
          onStyleChange={choose}
          onDownload={(fmt, s) => downloadExport(exportBase, fmt, s)}
          onClose={() => setDesignerOpen(false)}
        />
      ) : null}
    </>
  )
}
