/** Lazy pages that survive a deploy.
 *
 * Every build renames its chunks and the new image carries only the new ones.
 * A tab opened before a deploy still runs the old entry bundle, so the first
 * visit to a page it has not loaded yet asks for a chunk that is gone: nginx
 * answers 404 and the browser reports "error loading dynamically imported
 * module". index.html is served no-store, so a reload fetches the new entry
 * and the page opens. Only once per window, though: if the chunk is missing
 * right after a reload, the build itself is broken and looping would hide it
 * behind a flickering tab — the error goes on to the ErrorBoundary instead.
 */

import { lazy, type ComponentType } from 'react'

const RELOADED_AT = 'chunk-reload-at'
// Long enough to cover the reload itself, short enough that a second deploy
// later in the same tab gets its own reload.
const WINDOW_MS = 10_000

export function reloadOnce(now: number = Date.now()): boolean {
  let last = 0
  try {
    last = Number(sessionStorage.getItem(RELOADED_AT)) || 0
    if (now - last < WINDOW_MS) return false
    sessionStorage.setItem(RELOADED_AT, String(now))
  } catch {
    // Storage can be unavailable (private mode, blocked site data); without
    // the guard a reload could loop, so fall through to the error screen.
    return false
  }
  location.reload()
  return true
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- React.lazy itself is typed over any props
export function lazyPage<T extends ComponentType<any>>(load: () => Promise<T>) {
  return lazy(() =>
    load().then(
      (component) => ({ default: component }),
      (error: unknown) => {
        // A pending promise keeps Suspense on its fallback while the page
        // reloads, instead of flashing the error screen for a moment.
        if (reloadOnce()) return new Promise<never>(() => {})
        throw error
      },
    ),
  )
}
