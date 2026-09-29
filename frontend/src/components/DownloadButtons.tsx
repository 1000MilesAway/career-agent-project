/** "Скачать PDF / DOCX / MD" — one set of buttons, used on both screens.
 *
 * Shared so the two places cannot disagree on what a download costs. Nothing
 * here is paid: the server renders JSON that already exists, which is why no
 * price is shown next to these buttons while every paid button states one.
 */

import { useState } from 'react'
import type { ExportFormat } from '../api/hooks'
import { useToast } from './toast'

const LABELS: Record<ExportFormat, string> = { pdf: 'PDF', docx: 'DOCX', md: 'MD' }

export function DownloadButtons({
  onDownload,
  small = false,
  label = 'Скачать',
}: {
  onDownload: (fmt: ExportFormat) => Promise<void>
  small?: boolean
  /** Screen-reader context: two identical "PDF" buttons on one page otherwise. */
  label?: string
}) {
  // Which format is in flight, so only that button reads as busy.
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const toast = useToast()

  async function run(fmt: ExportFormat) {
    setBusy(fmt)
    try {
      await onDownload(fmt)
    } catch (e) {
      toast.error(e)
    } finally {
      setBusy(null)
    }
  }

  const className = small ? 'btn btn--ghost btn--small' : 'btn btn--ghost'

  return (
    <>
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
    </>
  )
}
