/** Asked once, before the cabinet opens, of an account without consent on record.
 *
 * Who lands here: a Telegram sign-up (the popup has no room for a checkbox)
 * and accounts made before consent was asked at all. An email sign-up ticked
 * the box on the form and never sees this.
 *
 * A screen rather than a banner: the first thing a new account does is upload
 * a CV, and the server refuses that without consent — a dismissable banner
 * would let the user walk into the refusal instead.
 */

import { useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, giveConsent } from '../../api/hooks'
import { useAuth } from '../../auth'
import { ErrorNote } from '../../components/ui'
import { LegalLinks } from './LegalPage'
import './legal.css'

export function ConsentScreen() {
  const { logout } = useAuth()
  const { mutate } = useSWRConfig()
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [isPending, startTransition] = useTransition()

  function onAgree() {
    setError(null)
    startTransition(async () => {
      try {
        await giveConsent()
        await mutate(STATE_KEY)
      } catch (e) {
        setError(e)
      }
    })
  }

  return (
    <main className="consent">
      <section className="consent__card" aria-labelledby="consent-title">
        <h1 id="consent-title">Прежде чем начать</h1>
        <p>
          Чтобы подбирать и оценивать вакансии, сервису нужно ваше резюме, а это
          персональные данные. Коротко, что с ними происходит:
        </p>
        <ul>
          <li>текст резюме и вакансий обрабатывает языковая модель DeepSeek (Китай);</li>
          <li>данные хранятся на сервере в Финляндии, письма отправляет Resend (США);</li>
          <li>никому больше данные не передаются и не продаются;</li>
          <li>удалить всё можно в любой момент в профиле.</li>
        </ul>
        <label className="check">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
          />
          <span>
            Даю{' '}
            <a href="#/legal/consent" target="_blank" rel="noreferrer">
              согласие на обработку персональных данных
            </a>{' '}
            и принимаю{' '}
            <a href="#/legal/terms" target="_blank" rel="noreferrer">
              пользовательское соглашение
            </a>
          </span>
        </label>
        <ErrorNote error={error} />
        <div className="consent__actions">
          <button
            type="button"
            className="btn"
            disabled={!agreed || isPending}
            onClick={onAgree}
          >
            {isPending ? 'Сохраняю…' : 'Продолжить'}
          </button>
          <button type="button" className="btn btn--ghost" onClick={logout}>
            Выйти
          </button>
        </div>
        <LegalLinks />
      </section>
    </main>
  )
}
