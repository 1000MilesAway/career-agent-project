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

const PHOTO_STORAGE_KEY = 'resume-photo'
const PHOTO_SWR_KEY = 'local:resume-photo'

function loadPhoto(): boolean {
  try {
    // On unless switched off: the user who uploaded a photo meant to use it.
    return localStorage.getItem(PHOTO_STORAGE_KEY) !== 'off'
  } catch {
    return true
  }
}

/** Whether the downloads put the uploaded photo into the header. The same
 *  per-browser memory as the style, for the same reasons. */
export function useResumePhotoChoice(): [boolean, (on: boolean) => void] {
  const { data, mutate } = useSWR<boolean>(PHOTO_SWR_KEY, loadPhoto, {
    fallbackData: loadPhoto(),
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
  })

  function choose(on: boolean) {
    try {
      localStorage.setItem(PHOTO_STORAGE_KEY, on ? 'on' : 'off')
    } catch {
      // Still applied for this session through SWR's cache.
    }
    void mutate(on, { revalidate: false })
  }

  return [data ?? true, choose]
}
