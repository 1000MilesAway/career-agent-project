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
