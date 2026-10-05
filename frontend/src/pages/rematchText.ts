import type { Rematch } from '../api/types'

/** The toast after a search profile change: what came into the found list,
 * what left it and why. The rows that left are not lost — their reasons are
 * on GET /api/vacancies/screened — but the user must hear they are gone. */
export function rematchText(lead: string, rematch: Rematch | null): string {
  if (!rematch || (rematch.added === 0 && rematch.removed === 0)) return lead
  const parts: string[] = []
  if (rematch.added) parts.push(`добавлено ${rematch.added}`)
  if (rematch.removed) {
    const why = rematch.reasons.map((r) => `${r.reason} — ${r.count}`).join('; ')
    parts.push(`убрано ${rematch.removed}${why ? ` (${why})` : ''}`)
  }
  return `${lead}. В поиске ${parts.join(', ')}.`
}
