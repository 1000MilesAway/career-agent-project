/** Where a confirmation letter's link lands: /#/verify?token=…
 *
 * The address is confirmed by a click here, not on page load. Mail services
 * and corporate filters open links from letters to scan them, and a scan
 * that confirmed the address would confirm it for whoever signed up — the
 * owner of the inbox would never have been asked.
 */

import { useState, useTransition } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorNote } from '../components/ui'
import { Pitch } from './LoginPage'
import './login.css'

export function VerifyPage() {
  const { verifyEmail } = useAuth()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const token = params.get('token')
  const [error, setError] = useState<unknown>(null)
  const [isPending, startTransition] = useTransition()

  function onConfirm() {
    if (!token) return
    setError(null)
    startTransition(async () => {
      try {
        await verifyEmail(token)
        navigate('/', { replace: true })
      } catch (e) {
        setError(e)
      }
    })
  }

  return (
    <main className="login">
      <Pitch />
      <section className="login__form">
        <h2 className="login__form-title">Подтверждение почты</h2>
        {token ? (
          <>
            <p style={{ margin: 0 }}>Осталось нажать кнопку — и кабинет откроется.</p>
            <button
              className="btn"
              type="button"
              onClick={onConfirm}
              disabled={isPending}
              style={{ height: 44 }}
            >
              {isPending ? 'Подтверждаю…' : 'Подтвердить почту'}
            </button>
          </>
        ) : (
          <p style={{ margin: 0 }}>
            В ссылке нет кода подтверждения — скопируйте её из письма целиком.
          </p>
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
