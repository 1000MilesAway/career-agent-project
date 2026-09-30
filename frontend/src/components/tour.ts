/** Which first-run hint the user is on, if any.
 *
 * Kept in the browser, not on the account: it is a convenience of this one
 * screen, and a hint missed on another device costs nothing. The tour starts
 * only from the first CV upload, so an existing account never sees it.
 */

import { useSyncExternalStore } from 'react'

export type TourStep = 'search' | 'star'

const KEY = 'career-agent:tour:v1'

function read(): TourStep | null {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'search' || value === 'star' ? value : null
  } catch {
    return null
  }
}

let current = read()
const listeners = new Set<() => void>()

export function setTourStep(step: TourStep | null) {
  current = step
  try {
    if (step) localStorage.setItem(KEY, step)
    else localStorage.removeItem(KEY)
  } catch {
    // Storage blocked: the tour lives until reload, which is enough.
  }
  listeners.forEach((listener) => listener())
}

export function useTourStep(): TourStep | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => current,
  )
}
