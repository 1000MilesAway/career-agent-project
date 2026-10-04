/** Where a reset letter's link lands: /reset-password?token=…
 *
 * Nothing happens on load: the new password has to be typed, so a mail
 * scanner opening the link changes nothing. Saving it logs in, and every
 * session from before the reset is logged out by the server.
 */

import { useState, useTransition, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorNote } from '../components/ui'
import { LoginHome } from './LoginPage'
import './login.css'

export function ResetPasswordPage() {
  const { resetPassword } = useAuth()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [isPending, startTransition] = useTransition()

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!token) return
    setError(null)
    startTransition(async () => {
      try {
        await resetPassword(token, password)
        navigate('/', { replace: true })
      } catch (e) {
        setError(e)
      }
    })
  }

  return (
    <main className="login">
      <LoginHome />
      <section className="login__form">
        <h2 className="login__form-title">Новый пароль</h2>
        {token ? (
          <form className="stack" style={{ gap: 14 }} onSubmit={onSubmit}>
            <label className="stack" style={{ gap: 6 }}>
              <span className="eyebrow" style={{ margin: 0 }}>
                Придумайте пароль
              </span>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                // The server's limits, as at signup.
                minLength={8}
                maxLength={72}
                required
                autoFocus
              />
              <span className="muted" style={{ fontSize: 13 }}>
                Не короче 8 символов. На других устройствах придётся войти заново.
              </span>
            </label>
            <button
              className="btn"
              type="submit"
              disabled={isPending}
              style={{ height: 44 }}
            >
              {isPending ? 'Сохраняю…' : 'Сохранить и войти'}
            </button>
          </form>
        ) : (
          <p style={{ margin: 0 }}>В ссылке нет кода — скопируйте её из письма целиком.</p>
        )}
        <ErrorNote error={error} />
        <p className="muted" style={{ marginBottom: 0 }}>
          <button
            type="button"
            className="login__switch"
            onClick={() => navigate('/', { replace: true })}
          >
            Ко входу
          </button>
        </p>
      </section>
    </main>
  )
}
