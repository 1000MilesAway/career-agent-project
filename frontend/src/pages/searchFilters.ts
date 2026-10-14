import type { FoundVacancy, TriageBucket } from '../api/types'
import { bucketOf } from '../components/VacancyRow'
import { formatSalary } from '../components/ui'

/** Which verdicts the list shows, and which folded groups are mixed in.
 *
 *  Kept in localStorage so a reload does not undo the user's choice. It is a
 *  per-browser view preference, not account state: nothing server-side reads
 *  it, and losing it (private window, cleared site data) only brings back the
 *  defaults. */

/** `null` is "no verdict yet" — neither triage nor a full evaluation. */
export type BucketKey = TriageBucket | null

/** "fresh" keeps the order rows were found in; "score" puts the evaluated
 *  ones first, best first. */
export type SortKey = 'fresh' | 'score'

export interface SearchFilters {
  buckets: BucketKey[]
  sort: SortKey
  /** Hide rows with a full evaluation — watch the queue, not the results.
   *  Inverted from an earlier «only evaluated» toggle: users asked for the
   *  opposite, so the stored key is renamed and the old value is not read
   *  (copying it would turn the old choice into its opposite). */
  hideScored: boolean
  showDropped: boolean
  /** Closed at the source, or too old to trust where the source cannot say. */
  showInactive: boolean
  /** Hidden by the user with «Скрыть». */
  showHidden: boolean
  /** Rows that do not meet the conditions above the list. */
  showMismatch: boolean
  conditions: Conditions
}

/** What the user asks of a vacancy. The same values go into the next scan's
 *  request and filter the rows already found, so the list never shows what
 *  the panel above it says to leave out. */
export interface Conditions {
  /** Monthly roubles; null — no threshold. */
  salary: number | null
  /** Keep rows that state no salary when a threshold is set. */
  anySalary: boolean
  remoteOnly: boolean
  accreditedOnly: boolean
  /** On-site cities, comma-separated as typed. null — never chosen here, so
   *  the city from the CV applies; '' — any city, chosen on purpose. */
  city: string | null
  /** A source choice, not a property of a row: the employer's group is not
   *  stored, so this one applies to the next scan only. */
  includeGlobal: boolean
}

export const DEFAULT_CONDITIONS: Conditions = {
  salary: null,
  anySalary: true,
  remoteOnly: true,
  accreditedOnly: false,
  city: null,
  includeGlobal: false,
}

const flag = (value: unknown, fallback: boolean) =>
  typeof value === 'boolean' ? value : fallback

const STORAGE_KEY = 'search-filters'
const KNOWN: readonly BucketKey[] = ['worth', 'maybe', 'skip', null]

/** Only "worth" is on by default — the point is a short list — and one click
 *  brings back any other verdict. */
export const DEFAULT_FILTERS: SearchFilters = {
  buckets: ['worth'],
  sort: 'fresh',
  hideScored: false,
  showDropped: false,
  showInactive: false,
  showHidden: false,
  showMismatch: false,
  conditions: DEFAULT_CONDITIONS,
}

function loadConditions(value: unknown): Conditions {
  if (typeof value !== 'object' || value === null) return DEFAULT_CONDITIONS
  const c = value as Partial<Record<keyof Conditions, unknown>>
  const salary =
    typeof c.salary === 'number' && Number.isFinite(c.salary) && c.salary > 0
      ? Math.round(c.salary)
      : null
  return {
    salary,
    anySalary: flag(c.anySalary, DEFAULT_CONDITIONS.anySalary),
    remoteOnly: flag(c.remoteOnly, DEFAULT_CONDITIONS.remoteOnly),
    accreditedOnly: flag(c.accreditedOnly, DEFAULT_CONDITIONS.accreditedOnly),
    city: typeof c.city === 'string' ? c.city : null,
    includeGlobal: flag(c.includeGlobal, DEFAULT_CONDITIONS.includeGlobal),
  }
}

/** The cities typed into the box, one per comma. */
export const splitCities = (text: string | null): string[] =>
  (text ?? '')
    .split(',')
    .map((city) => city.trim())
    .filter(Boolean)

/** Spellings of one city that boards really use. The same table as
 *  CITY_ALIASES in core/filters.py: edit both, or the server keeps a row the
 *  list folds away, or the reverse. */
const CITY_ALIASES: readonly (readonly string[])[] = [
  ['москва', 'moscow', 'мск'],
  [
    'санкт-петербург',
    'спб',
    'питер',
    'петербург',
    'saint petersburg',
    'st. petersburg',
    'st petersburg',
  ],
  ['екатеринбург', 'ekaterinburg', 'yekaterinburg', 'екб'],
  ['новосибирск', 'novosibirsk'],
  ['казань', 'kazan'],
  ['нижний новгород', 'nizhny novgorod'],
]

const cityKey = (text: string) =>
  text
    .toLowerCase()
    .replaceAll('ё', 'е')
    .trim()
    .replace(/^г\.?\s+|^город\s+/, '')

const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Does a location name one of the cities? A name must start a word, so
 *  "Москва" does not match "Новомосковск". No cities — any location. */
export function cityMatches(location: string, cities: string[]): boolean {
  const names = new Set<string>()
  for (const city of cities) {
    const key = cityKey(city)
    if (!key) continue
    for (const name of CITY_ALIASES.find((aliases) => aliases.includes(key)) ?? [key])
      names.add(name)
  }
  if (names.size === 0) return true
  const loc = cityKey(location)
  return [...names].some((name) =>
    new RegExp(`(?<![\\p{L}\\p{N}_-])${escapeRe(name)}`, 'u').test(loc),
  )
}

export function loadFilters(): SearchFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_FILTERS
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_FILTERS
    const {
      buckets,
      sort,
      hideScored,
      showDropped,
      showInactive,
      showHidden,
      showMismatch,
      conditions,
    } = parsed as Partial<Record<keyof SearchFilters, unknown>>
    // A value written by an older build may name a bucket that no longer
    // exists; drop it rather than keep a filter no button can switch off.
    return {
      buckets: Array.isArray(buckets)
        ? KNOWN.filter((key) => buckets.includes(key))
        : DEFAULT_FILTERS.buckets,
      sort: sort === 'score' ? 'score' : DEFAULT_FILTERS.sort,
      hideScored: flag(hideScored, DEFAULT_FILTERS.hideScored),
      showDropped: flag(showDropped, DEFAULT_FILTERS.showDropped),
      showInactive: flag(showInactive, DEFAULT_FILTERS.showInactive),
      showHidden: flag(showHidden, DEFAULT_FILTERS.showHidden),
      showMismatch: flag(showMismatch, DEFAULT_FILTERS.showMismatch),
      conditions: loadConditions(conditions),
    }
  } catch {
    return DEFAULT_FILTERS
  }
}

export function saveFilters(filters: SearchFilters): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(filters))
  } catch {
    // Storage blocked or full: the choice still holds until the next reload.
  }
}

/** Unscored rows keep the verdict order the bucket buttons show, so sorting
 *  by score still leaves "стоит посмотреть" ahead of "может быть". */
const BUCKET_RANK: Record<string, number> = { worth: 0, maybe: 1, skip: 2 }
const bucketRank = (row: FoundVacancy) => BUCKET_RANK[bucketOf(row) ?? ''] ?? 3

/** Rows in the chosen order. Array.sort is stable, so ties — and every row
 *  under "fresh" — keep the order the server sent. */
export function sortRows(rows: FoundVacancy[], sort: SortKey): FoundVacancy[] {
  if (sort === 'fresh') return rows
  return [...rows].sort((a, b) => {
    if (a.score != null && b.score != null) return b.score - a.score
    if (a.score != null) return -1
    if (b.score != null) return 1
    return bucketRank(a) - bucketRank(b)
  })
}

/** Why a found row does not meet the conditions, or null when it does.
 *
 *  The same three-valued rules as the free filter on the server
 *  (core/filters.py, PreScreen): only an explicit "no" fails — a source that
 *  says nothing about remote work or accreditation is not punished for it.
 *  The reason is a line the row shows, so a folded row is never unexplained. */
export function conditionMiss(row: FoundVacancy, c: Conditions): string | null {
  if (c.remoteOnly && row.remote === false) return 'не удалённая работа'
  // Remote work is done from anywhere, and a row naming no city is not
  // punished for the silence — PreScreen's rule.
  if (
    row.remote !== true &&
    row.location &&
    !cityMatches(row.location, splitCities(c.city))
  )
    return `другой город: ${row.location}`
  if (c.salary) {
    const [from, to] = row.salary_range ?? [null, null]
    const top = to ?? from
    if (top != null && top < c.salary) return `зарплата ниже ${formatSalary(c.salary)} ₽`
    if (top == null && !c.anySalary) return 'зарплата не указана'
  }
  if (c.accreditedOnly && row.accredited === false) return 'ИТ-аккредитация не подтверждена'
  return null
}
