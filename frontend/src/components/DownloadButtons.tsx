/** "Оформить · PDF / DOCX / MD" — one set of buttons, used on both screens.
 *
 * Shared so the two places cannot disagree on what a download costs. Nothing
 * here is paid: the server renders JSON that already exists.
 *
 * The quick buttons download in the style last picked in the designer, so a
 * user who chose "Акцент" once does not have to open the designer again for
 * every vacancy.
 */

import { useState } from 'react'
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
  /** Screen-reader context: two identical "PDF" buttons on one page otherwise. */
  label?: string
  designerTitle?: string
}) {
  // Which format is in flight, so only that button reads as busy.
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const [designerOpen, setDesignerOpen] = useState(false)
  const [chosen, choose] = useResumeStyleChoice()
  const { data: styles } = useResumeStyles()
  const toast = useToast()

  // A remembered style the server no longer offers would answer 422 on every
  // download; fall back to the default instead of breaking the buttons.
  const style = !styles || styles.some((s) => s.id === chosen) ? chosen : DEFAULT_STYLE

  async function run(fmt: ExportFormat) {
    setBusy(fmt)
    try {
      await downloadExport(exportBase, fmt, style)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }

  const className = small ? 'btn btn--ghost btn--small' : 'btn btn--ghost'
  const styleName = styles?.find((s) => s.id === style)?.name

  return (
    <>
      <button
        className={className}
        onClick={() => setDesignerOpen(true)}
        aria-label={`${label} — выбрать оформление`}
        title={
          styleName ? `Оформление: ${styleName}. Выбрать другое` : 'Выбрать оформление'
        }
      >
        Оформить
      </button>
      {(['pdf', 'docx', 'md'] as const).map((fmt) => (
        <button
          key={fmt}
          className={className}
          onClick={() => run(fmt)}
          disabled={busy !== null}
          aria-label={`${label} — ${LABELS[fmt]}`}
          title={`${label} в формате ${LABELS[fmt]}`}
        >
          {busy === fmt ? 'Готовлю…' : LABELS[fmt]}
        </button>
      ))}
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
