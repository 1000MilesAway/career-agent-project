/** The one place that knows how to talk to the API.
 *
 * Every call attaches the bearer token and normalises failures, so no call
 * site has to remember either. A 401 clears the token and notifies the app,
 * because every other outcome leaves the user clicking buttons that silently
 * do nothing.
 */

/** Versioned: a stored value from an older shape must not be read as this one. */
const TOKEN_KEY = 'career-agent:token:v1'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// Kept in a module variable as well as in storage: storage throws in private
// mode, and the session should still work until the tab is closed.
let token: string | null = readStoredToken()

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

type Listener = (token: string | null) => void
const listeners = new Set<Listener>()

/** Subscribe to login/logout, including the logout a 401 forces. */
export function onTokenChange(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getToken(): string | null {
  return token
}

export function setToken(value: string | null): void {
  token = value
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage blocked. The session simply ends on reload.
  }
  for (const listener of listeners) listener(value)
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  json?: unknown
  form?: FormData
  signal?: AbortSignal
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', json, form, signal } = options
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  let body: BodyInit | undefined
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(json)
  } else if (form) {
    // No Content-Type: the browser has to set the multipart boundary itself.
    body = form
  }

  const response = await fetch(path, { method, headers, body, signal })

  if (response.status === 401) {
    setToken(null)
    throw new ApiError('Нужно войти заново', 401)
  }

  const text = await response.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }

  if (!response.ok) {
    throw new ApiError(errorMessage(data, response.status), response.status)
  }
  return data as T
}

/** Download a file from the API and hand it to the browser's save dialog.
 *
 * A plain <a href> cannot be used: the token travels in the Authorization
 * header, and a bare link arrives unauthenticated — the user would get a 401
 * page instead of a file. So the bytes are fetched like any other call and
 * handed over as a blob.
 *
 * The name comes from Content-Disposition when the server sent one, because
 * only the server knows whose CV this is and which vacancy it was tailored
 * for. `fallbackName` covers a proxy that strips the header.
 */
export async function download(path: string, fallbackName: string): Promise<void> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(path, { headers })

  if (response.status === 401) {
    setToken(null)
    throw new ApiError('Нужно войти заново', 401)
  }
  if (!response.ok) {
    // An error body is JSON even on a route that normally returns bytes.
    let data: unknown = null
    try {
      data = await response.json()
    } catch {
      data = null
    }
    throw new ApiError(errorMessage(data, response.status), response.status)
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download =
      filenameFrom(response.headers.get('Content-Disposition')) || fallbackName
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    // Revoking immediately can cancel the download in some browsers; a tick
    // later the navigation has already been handed off.
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}

/** A text body with the token: the resume preview is HTML, not JSON.
 *
 * An <iframe src> cannot carry the Authorization header either, so the page
 * is fetched here and handed to the iframe as `srcdoc`.
 */
export async function fetchText(path: string): Promise<string> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(path, { headers })

  if (response.status === 401) {
    setToken(null)
    throw new ApiError('Нужно войти заново', 401)
  }
  const text = await response.text()
  if (!response.ok) {
    let data: unknown = null
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
    throw new ApiError(errorMessage(data, response.status), response.status)
  }
  return text
}

/** The name out of Content-Disposition, preferring RFC 5987 `filename*`. */
function filenameFrom(header: string | null): string | null {
  if (!header) return null

  const starred = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (starred) {
    try {
      return decodeURIComponent(starred[1])
    } catch {
      // Malformed percent-encoding: fall through to the plain form.
    }
  }

  const plain = /filename="?([^";]+)"?/i.exec(header)
  return plain ? plain[1] : null
}

/** FastAPI puts the message in `detail` — a string, or a list for a 422. */
function errorMessage(data: unknown, status: number): string {
  const detail = (data as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (item as { msg?: string }).msg)
      .filter((msg): msg is string => Boolean(msg))
    if (parts.length) return parts.join('; ')
  }
  if (status === 402) return 'Бюджет исчерпан — платные шаги недоступны'
  return `Ошибка ${status}`
}

/** The fetcher SWR calls. Separate so hooks stay one line. */
export const fetcher = <T>(path: string): Promise<T> => api<T>(path)
