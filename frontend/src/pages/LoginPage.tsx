/** The gate. Nothing else renders until a token is in hand.
 *
 * Three states of one form: log in, sign up, and "the letter is on its way".
 * Signing up is offered only when the server can send mail; without it the
 * way in for a newcomer is Telegram.
 */

import { useState, useTransition, type FormEvent, type ReactNode } from 'react'
import useSWR from 'swr'
import { api, fetcher } from '../api/client'
import { useAuth } from '../auth'
import { TelegramButton, useTelegramConfig } from '../components/TelegramLogin'
import { ErrorNote } from '../components/ui'
import './login.css'

type Mode = 'login' | 'signup' | 'sent'

interface SignupConfig {
  enabled: boolean
}

export function LoginPage() {
  const { login, loginTelegram } = useAuth()
  const telegram = useTelegramConfig()
  const { data: signupConfig } = useSWR<SignupConfig>('/api/auth/signup', fetcher, {
    revalidateOnFocus: false,
  })
  const signupEnabled = signupConfig?.enabled ?? false
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [sentTo, setSentTo] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  function run(action: () => Promise<void>) {
    setError(null)
    setNotice(null)
    startTransition(async () => {
      try {
        await action()
      } catch (e) {
        setError(e)
      }
    })
  }

  function switchTo(next: Mode) {
    setError(null)
    setNotice(null)
    setMode(next)
  }

  // Also the "send again" button: a repeat signup for an unconfirmed address
  // is how the server sends a fresh letter.
  async function signup() {
    const data = await api<{ email: string }>('/api/auth/signup', {
      method: 'POST',
      json: { email: email.trim(), password },
    })
    setSentTo(data.email)
    setMode('sent')
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    run(() => (mode === 'signup' ? signup() : login(email, password)))
  }

  function onResend() {
    run(async () => {
      await signup()
      setNotice('Отправили ещё одно письмо')
    })
  }

  function onTelegram(idToken: string) {
    run(() => loginTelegram(idToken))
  }

  return (
    <main className="login">
      <LoginHome />

      <section className="login__form">
        {mode === 'sent' ? (
          <>
            <h2 className="login__form-title">Проверьте почту</h2>
            <p style={{ margin: 0 }}>
              Письмо со ссылкой ушло на <b>{sentTo}</b>. Откройте его — ссылка подтвердит
              почту и сразу откроет кабинет.
            </p>
            <p className="muted" style={{ margin: 0 }}>
              Ссылка действует сутки. Не видно письма — загляните в «Спам».
            </p>
            <button
              className="btn btn--ghost"
              type="button"
              onClick={onResend}
              disabled={isPending}
              style={{ height: 44 }}
            >
              {isPending ? 'Отправляю…' : 'Отправить ещё раз'}
            </button>
            {notice ? (
              <p className="muted" role="status" style={{ margin: 0 }}>
                {notice}
              </p>
            ) : null}
            <ErrorNote error={error} />
            <p className="muted" style={{ marginBottom: 0 }}>
              <SwitchLink onClick={() => switchTo('signup')}>Другой адрес</SwitchLink>
              {' · '}
              <SwitchLink onClick={() => switchTo('login')}>Ко входу</SwitchLink>
            </p>
          </>
        ) : (
          <>
            <h2 className="login__form-title">
              {mode === 'signup' ? 'Регистрация' : 'Вход'}
            </h2>
            {/* A real <form>: Enter submits, and password managers recognise it. */}
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
                  autoComplete={mode === 'signup' ? 'email' : 'username'}
                  required
                  autoFocus
                />
              </label>
              <label className="stack" style={{ gap: 6 }}>
                <span className="eyebrow" style={{ margin: 0 }}>
                  {mode === 'signup' ? 'Придумайте пароль' : 'Пароль'}
                </span>
                <input
                  className="input"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                  // The server's limits; checked here so a short password is
                  // caught before the round trip.
                  minLength={mode === 'signup' ? 8 : undefined}
                  maxLength={mode === 'signup' ? 72 : undefined}
                  required
                />
                {mode === 'signup' ? (
                  <span className="muted" style={{ fontSize: 13 }}>
                    Не короче 8 символов
                  </span>
                ) : null}
              </label>
              <button
                className="btn"
                type="submit"
                disabled={isPending}
                style={{ height: 44, marginTop: 4 }}
              >
                {mode === 'signup'
                  ? isPending
                    ? 'Отправляю письмо…'
                    : 'Зарегистрироваться'
                  : isPending
                    ? 'Вхожу…'
                    : 'Войти'}
              </button>
            </form>

            {/* Logs into the linked account, or creates one on the first visit. */}
            {telegram ? (
              <div className="login__telegram">
                <span className="login__or">или</span>
                <TelegramButton
                  config={telegram}
                  onToken={onTelegram}
                  onError={setError}
                  disabled={isPending}
                >
                  Войти через Telegram
                </TelegramButton>
              </div>
            ) : null}

            <ErrorNote error={error} />

            <p className="muted" style={{ marginBottom: 0 }}>
              {mode === 'signup' ? (
                <>
                  Уже есть учётка?{' '}
                  <SwitchLink onClick={() => switchTo('login')}>Войти</SwitchLink>
                </>
              ) : signupEnabled ? (
                <>
                  Впервые здесь?{' '}
                  <SwitchLink onClick={() => switchTo('signup')}>
                    Зарегистрируйтесь по почте
                  </SwitchLink>
                  {telegram ? ' или войдите через Telegram — учётка создастся сама.' : '.'}
                </>
              ) : (
                'Впервые здесь? Войдите через Telegram — учётка создастся сама.'
              )}
            </p>

            <p className="login__legal">
              Входя, вы принимаете <a href="#/terms">пользовательское соглашение</a> и{' '}
              <a href="#/privacy">политику конфиденциальности</a>.
            </p>
          </>
        )}
      </section>
    </main>
  )
}

function SwitchLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className="login__switch" onClick={onClick}>
      {children}
    </button>
  )
}

/** The way back to the landing: #/ with no token is the landing (see index.html).
 * The pitch itself lives there now, so the gate carries only the form. */
export function LoginHome() {
  return (
    <a className="brand login__home" href="#/" title="О сервисе">
      <span className="brand__mark" aria-hidden="true" />
      <span className="brand__name">career-agent</span>
    </a>
  )
}
