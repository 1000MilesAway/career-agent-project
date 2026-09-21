/** The gate. Nothing else renders until a token is in hand. */

import { useState, useTransition, type FormEvent } from 'react'
import { useAuth } from '../auth'
import { Card, ErrorNote } from '../components/ui'

export function LoginPage() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [isPending, startTransition] = useTransition()

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    startTransition(async () => {
      try {
        await login(email, password)
      } catch (e) {
        setError(e)
      }
    })
  }

  return (
    <main className="wrap" style={{ maxWidth: 420 }}>
      <h1 style={{ fontSize: 20, marginBottom: 4 }}>career-agent</h1>
      <p className="muted" style={{ marginTop: 0, marginBottom: 20 }}>
        Оценка вакансий по резюме. Отклик отправляете вы сами.
      </p>

      <Card title="Вход">
        {/* A real <form>: Enter submits, and password managers recognise it.
            The old page wired keydown handlers onto both inputs by hand. */}
        <form className="stack" onSubmit={onSubmit}>
          <label className="stack" style={{ gap: 4 }}>
            <span className="muted">Email</span>
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              autoFocus
            />
          </label>
          <label className="stack" style={{ gap: 4 }}>
            <span className="muted">Пароль</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <button className="btn" type="submit" disabled={isPending}>
            {isPending ? 'Вхожу…' : 'Войти'}
          </button>
        </form>

        <ErrorNote error={error} />

        <p className="muted" style={{ marginBottom: 0 }}>
          Регистрации нет — учётку заводит администратор.
        </p>
      </Card>
    </main>
  )
}
