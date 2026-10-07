/** Who is logged in, for the whole tree.
 *
 * The token lives in the api client; this only mirrors it into React state so
 * a forced logout (a 401 from any call) re-renders the app back to the login
 * screen instead of leaving a dead cabinet on screen.
 */

import { createContext, use, useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, onTokenChange, setToken } from './api/client'

interface AuthValue {
  token: string | null
  login: (email: string, password: string) => Promise<void>
  /** `consent` is read only when the login would create the account. */
  loginTelegram: (idToken: string, consent?: boolean) => Promise<void>
  /** Opens a letter's link: confirms the address and logs in. */
  verifyEmail: (token: string) => Promise<void>
  /** Opens a reset letter's link: sets the new password and logs in. */
  resetPassword: (token: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(getToken)

  useEffect(() => onTokenChange(setTokenState), [])

  const login = useCallback(async (email: string, password: string) => {
    const data = await api<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      json: { email: email.trim(), password },
    })
    setToken(data.access_token)
  }, [])

  const loginTelegram = useCallback(async (idToken: string, consent = false) => {
    const response = await api<{ access_token: string }>('/api/auth/telegram', {
      method: 'POST',
      json: { id_token: idToken, consent },
    })
    setToken(response.access_token)
  }, [])

  const verifyEmail = useCallback(async (token: string) => {
    const response = await api<{ access_token: string }>('/api/auth/verify', {
      method: 'POST',
      json: { token },
    })
    setToken(response.access_token)
  }, [])

  const resetPassword = useCallback(async (token: string, password: string) => {
    const response = await api<{ access_token: string }>(
      '/api/auth/password-reset/confirm',
      {
        method: 'POST',
        json: { token, password },
      },
    )
    setToken(response.access_token)
  }, [])

  const logout = useCallback(() => setToken(null), [])

  return (
    <AuthContext
      value={{ token, login, loginTelegram, verifyEmail, resetPassword, logout }}
    >
      {children}
    </AuthContext>
  )
}

export function useAuth(): AuthValue {
  const value = use(AuthContext)
  if (!value) throw new Error('useAuth вызван вне AuthProvider')
  return value
}
