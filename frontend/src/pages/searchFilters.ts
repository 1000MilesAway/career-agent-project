import type { FoundVacancy, TriageBucket } from '../api/types'

/** Which verdicts the list shows, and which folded groups are mixed in.
 *
 *  Kept in localStorage so a reload does not undo the user's choice. It is a
 *  per-browser view preference, not account state: nothing server-side reads
 *  it, and losing it (private window, cleared site data) only brings back the
 *  defaults. */

/** `null` is "no verdict yet". */
export type BucketKey = TriageBucket | null

/** "fresh" keeps the order rows were found in; "score" puts the evaluated
 *  ones first, best first. */
export type SortKey = 'fresh' | 'score'

export interface SearchFilters {
  buckets: BucketKey[]
  sort: SortKey
  /** Only rows with a full evaluation — the result, without the queue. */
  scoredOnly: boolean
  showDropped: boolean
  /** Closed at the source, or too old to trust where the source cannot say. */
  showInactive: boolean
  /** Hidden by the user with «Скрыть». */
  showHidden: boolean
}

const STORAGE_KEY = 'search-filters'
const KNOWN: readonly BucketKey[] = ['worth', 'maybe', 'skip', null]

/** Only "worth" is on by default — the point is a short list — and one click
 *  brings back any other verdict. */
export const DEFAULT_FILTERS: SearchFilters = {
  buckets: ['worth'],
  sort: 'fresh',
  scoredOnly: false,
  showDropped: false,
  showInactive: false,
  showHidden: false,
}

const flag = (value: unknown, fallback: boolean) =>
  typeof value === 'boolean' ? value : fallback

export function loadFilters(): SearchFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_FILTERS
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_FILTERS
    const { buckets, sort, scoredOnly, showDropped, showInactive, showHidden } =
      parsed as Partial<Record<keyof SearchFilters, unknown>>
    // A value written by an older build may name a bucket that no longer
    // exists; drop it rather than keep a filter no button can switch off.
    return {
      buckets: Array.isArray(buckets)
        ? KNOWN.filter((key) => buckets.includes(key))
        : DEFAULT_FILTERS.buckets,
      sort: sort === 'score' ? 'score' : DEFAULT_FILTERS.sort,
      scoredOnly: flag(scoredOnly, DEFAULT_FILTERS.scoredOnly),
      showDropped: flag(showDropped, DEFAULT_FILTERS.showDropped),
      showInactive: flag(showInactive, DEFAULT_FILTERS.showInactive),
      showHidden: flag(showHidden, DEFAULT_FILTERS.showHidden),
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
const bucketRank = (row: FoundVacancy) => BUCKET_RANK[row.triage?.bucket ?? ''] ?? 3

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
