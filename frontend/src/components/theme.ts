/** Light, dark, or as the system says — the viewer's own choice.
 *
 * Stored in the browser, not the account: it is about this screen, and a
 * phone in the sun and a laptop at night may want different ones. The choice
 * is applied as `data-theme` on <html>; with none the CSS follows
 * `prefers-color-scheme`. index.html applies the stored value before the
 * first paint, so a dark page does not flash white on load.
 */

import { useState } from 'react'

export type Theme = 'auto' | 'light' | 'dark'

// Mirrored in the script in index.html.
const KEY = 'career-agent:theme'

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'auto'
  } catch {
    return 'auto'
  }
}

function apply(theme: Theme) {
  const root = document.documentElement
  if (theme === 'auto') delete root.dataset.theme
  else root.dataset.theme = theme
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState(stored)

  function choose(next: Theme) {
    setTheme(next)
    apply(next)
    try {
      if (next === 'auto') localStorage.removeItem(KEY)
      else localStorage.setItem(KEY, next)
    } catch {
      // Private mode: the choice holds for this page only.
    }
  }

  return [theme, choose]
}
