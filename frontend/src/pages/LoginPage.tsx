/** The gate. Nothing else renders until a token is in hand. */

import { useState, useTransition, type FormEvent } from 'react'
import { useAuth } from '../auth'
import { ErrorNote } from '../components/ui'
import './login.css'

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
    <main className="login">
      <section className="login__pitch">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">career-agent</span>
        </div>
        <h1 className="login__title">
          Сотни вакансий — <em>короткий список</em> тех, что стоят отклика.
        </h1>
        <ol className="login__steps">
          <li>
            <b>Ищет везде сразу.</b> Хабр Карьера, сайты компаний, geekjob и
            Telegram-каналы.
          </li>
          <li>
            <b>Отсеивает бесплатно.</b> Каждая выброшенная вакансия — с причиной одной
            строкой.
          </li>
          <li>
            <b>Оценивает по резюме.</b> Что совпало, чего не хватает, и что с этим делать.
          </li>
        </ol>
        <p className="login__promise">Отклик всегда отправляете вы сами.</p>
      </section>

      <section className="login__form">
        <h2 className="login__form-title">Вход</h2>
        {/* A real <form>: Enter submits, and password managers recognise it.
            The old page wired keydown handlers onto both inputs by hand. */}
        <form className="stack" style={{ gap: 14 }} onSubmit={onSubmit}>
          <label className="stack" style={{ gap: 6 }}>
            <span className="eyebrow" style={{ margin: 0 }}>
              Email
            </span>
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
          <label className="stack" style={{ gap: 6 }}>
            <span className="eyebrow" style={{ margin: 0 }}>
              Пароль
            </span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <button
            className="btn"
            type="submit"
            disabled={isPending}
            style={{ height: 44, marginTop: 4 }}
          >
            {isPending ? 'Вхожу…' : 'Войти'}
          </button>
        </form>

        <ErrorNote error={error} />

        <p className="muted" style={{ marginBottom: 0 }}>
          Регистрации пока нет — учётку заводит администратор.
        </p>
      </section>
    </main>
  )
}
