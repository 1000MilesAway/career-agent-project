/** Number and date formatting for the owner's dashboard.
 *
 * Money here is the owner's cost, which runs to fractions of a rouble per
 * call: a fixed two decimals would show most rows as «0,00 ₽». Precision
 * grows as the amount shrinks instead. */

const rubFormats = [0, 2, 3].map(
  (digits) =>
    new Intl.NumberFormat('ru-RU', {
      style: 'currency',
      currency: 'RUB',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }),
)

export function formatRub(value: number): string {
  const abs = Math.abs(value)
  const format =
    abs >= 100 ? rubFormats[0] : abs >= 1 || abs === 0 ? rubFormats[1] : rubFormats[2]
  return format.format(value)
}

const usdFormat = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
})

export const formatUsd = (value: number): string => usdFormat.format(value)

const countFormat = new Intl.NumberFormat('ru-RU')
const compactFormat = new Intl.NumberFormat('ru-RU', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

export const formatCount = (value: number): string => countFormat.format(value)

/** «1,2 млн» in tiles, where the exact figure is in the tooltip. */
export const formatCompact = (value: number): string => compactFormat.format(value)

export const formatPct = (part: number, whole: number): string =>
  whole > 0 ? `${Math.round((part / whole) * 100)}%` : '—'

/** Moscow time: the quota days and the dashboard's days turn over there. */
const MSK = 'Europe/Moscow'

export const formatDate = (iso: string): string =>
  new Date(iso).toLocaleDateString('ru-RU', {
    timeZone: MSK,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })

export const formatDateTime = (iso: string): string =>
  new Date(iso).toLocaleString('ru-RU', {
    timeZone: MSK,
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

/** A Moscow calendar day as the API sends it, YYYY-MM-DD. Parsed at noon
 * UTC so no time zone can move it to a neighbouring date. */
export const formatDay = (day: string, options: Intl.DateTimeFormatOptions): string =>
  new Date(`${day}T12:00:00Z`).toLocaleDateString('ru-RU', { timeZone: 'UTC', ...options })

/** Step names as `core/` writes them, in words the owner reads. Unknown
 * steps show their raw name rather than disappearing. */
export const STEP_TITLES: Record<string, string> = {
  parse_resume: 'Разбор резюме',
  archetypes: 'Архетипы из резюме',
  search_profile: 'Профиль поиска',
  triage: 'Умный отбор',
  evaluate: 'Оценка вакансии',
  tailor_resume: 'Резюме под вакансию',
  cover_letter: 'Сопроводительное письмо',
  interview_prep: 'Подготовка к интервью',
  chat: 'Помощник',
}

export const stepTitle = (step: string): string => STEP_TITLES[step] ?? step
