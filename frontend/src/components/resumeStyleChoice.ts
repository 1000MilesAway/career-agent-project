/** The resume style the user picked last, shared by every download button.
 *
 * Kept in localStorage: it is a per-browser convenience, and losing it costs
 * one click in the designer, so it does not earn a column in the database.
 * Held in SWR under a local key so that picking a style in the designer
 * updates every download button on the page at once, not just the one that
 * opened it.
 */

import useSWR from 'swr'

const STORAGE_KEY = 'resume-style'
const SWR_KEY = 'local:resume-style'
export const DEFAULT_STYLE = 'classic'

function load(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_STYLE
  } catch {
    // Private mode or blocked storage: the default is a fine answer.
    return DEFAULT_STYLE
  }
}

export function useResumeStyleChoice(): [string, (style: string) => void] {
  const { data, mutate } = useSWR<string>(SWR_KEY, load, {
    fallbackData: load(),
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
  })

  function choose(style: string) {
    try {
      localStorage.setItem(STORAGE_KEY, style)
    } catch {
      // Still applied for this session through SWR's cache.
    }
    void mutate(style, { revalidate: false })
  }

  return [data ?? DEFAULT_STYLE, choose]
}
