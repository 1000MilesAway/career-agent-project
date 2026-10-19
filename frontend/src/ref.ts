/** Where a visitor came from, carried from the first visit to the signup.
 *
 * Links in posts and messages end with `?ref=vc` — the short form Product Hunt
 * and Hacker News use; `utm_source` is read too, because ad cabinets append
 * it themselves. The first label seen wins: whoever brought the person here
 * first gets the credit, not the bookmark they came back by.
 *
 * The parameters are taken out of the address bar, so a copied link does not
 * pass this visitor's source on to the next person.
 */

const KEY = 'career-agent:ref'
const PARAMS = [
  'ref',
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_content',
  'utm_term',
]
// Mirrors _REF in app/api/auth.py; the server drops anything else anyway.
const LABEL = /^[a-z0-9_.-]{1,32}$/

export function captureRef(): void {
  const url = new URL(location.href)
  const label = (url.searchParams.get('ref') ?? url.searchParams.get('utm_source') ?? '')
    .trim()
    .toLowerCase()
  if (LABEL.test(label)) {
    try {
      if (!localStorage.getItem(KEY)) localStorage.setItem(KEY, label)
    } catch {
      // Storage blocked: the signup simply comes without a source.
    }
  }
  if (PARAMS.some((name) => url.searchParams.has(name))) {
    PARAMS.forEach((name) => url.searchParams.delete(name))
    history.replaceState(history.state, '', url.pathname + url.search + url.hash)
  }
}

export function storedRef(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
