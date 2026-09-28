/** A list that does not move under the user's hand.
 *
 * The order is taken once — on opening the page, on changing the sort or a
 * filter, after a scan — and then held. A row that is evaluated, hidden or
 * re-judged changes in place; the list is rearranged only when the user asks.
 * Sorting by score otherwise flew a freshly evaluated row to the top, and the
 * user lost both the row and the place in the list they had reached.
 */

import type { FoundVacancy } from '../api/types'

export interface FrozenOrder {
  /** The view the order was taken for: sort, filters, and a manual epoch. */
  key: string
  /** The scan whose rows the order already includes. */
  scanMark: string | null
  ids: string[]
}

/** Rows in the held order. A held row that no longer passes the filters stays
 *  where it was — it leaves on the next rearrangement, not under the cursor.
 *  One that is gone from the payload altogether (the list was cleared) is
 *  dropped. Rows the order has not seen yet go last. */
export function arrange(
  heldIds: readonly string[],
  rows: readonly FoundVacancy[],
  live: readonly FoundVacancy[],
): FoundVacancy[] {
  const byId = new Map(rows.map((row) => [row.id, row]))
  const held = new Set(heldIds)
  const out: FoundVacancy[] = []
  for (const id of heldIds) {
    const row = byId.get(id)
    if (row) out.push(row)
  }
  for (const row of live) if (!held.has(row.id)) out.push(row)
  return out
}

/** How many rows a rearrangement would move, add or take away: the shown
 *  length minus the longest run already in the live order. One evaluated row
 *  counts as one, not as every row it would push down. */
export function pendingChanges(
  shownIds: readonly string[],
  liveIds: readonly string[],
): number {
  const liveIndex = new Map(liveIds.map((id, index) => [id, index]))
  // Patience sorting: tails[k] is the smallest tail of an increasing run of
  // length k + 1, so the longest run is found in n log n over a few hundred rows.
  const tails: number[] = []
  for (const id of shownIds) {
    const index = liveIndex.get(id)
    if (index === undefined) continue
    let lo = 0
    let hi = tails.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (tails[mid] < index) lo = mid + 1
      else hi = mid
    }
    tails[lo] = index
  }
  return shownIds.length - tails.length
}
