import type { TriageBucket } from '../api/types'

/** Which verdicts the list shows, and whether dropped rows are mixed in.
 *
 *  Kept in localStorage so a reload does not undo the user's choice. It is a
 *  per-browser view preference, not account state: nothing server-side reads
 *  it, and losing it (private window, cleared site data) only brings back the
 *  defaults. */

/** `null` is "no verdict yet". */
export type BucketKey = TriageBucket | null

export interface SearchFilters {
  buckets: BucketKey[]
  showDropped: boolean
}

const STORAGE_KEY = 'search-filters'
const KNOWN: readonly BucketKey[] = ['worth', 'maybe', 'skip', null]

/** "skip" is off by default — the point is a short list — but one click
 *  brings it back. */
export const DEFAULT_FILTERS: SearchFilters = {
  buckets: ['worth', 'maybe', null],
  showDropped: false,
}

export function loadFilters(): SearchFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_FILTERS
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_FILTERS
    const { buckets, showDropped } = parsed as Partial<Record<keyof SearchFilters, unknown>>
    // A value written by an older build may name a bucket that no longer
    // exists; drop it rather than keep a filter no button can switch off.
    return {
      buckets: Array.isArray(buckets)
        ? KNOWN.filter((key) => buckets.includes(key))
        : DEFAULT_FILTERS.buckets,
      showDropped:
        typeof showDropped === 'boolean' ? showDropped : DEFAULT_FILTERS.showDropped,
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
