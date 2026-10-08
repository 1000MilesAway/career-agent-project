/** What the user sees instead of a page that could not be shown.
 *
 * Two causes, one look. The server not answering (a deploy restarts it for
 * a minute, or the network dropped) and the page itself crashing. Both used
 * to show a raw message — «Ошибка 502» alone in a corner, a stack-trace box —
 * which reads as "everything is lost". The screen says what happened in
 * plain words, that the data is safe, and offers the one action that helps.
 * The technical text stays available, folded, for a report to support.
 */

import { useState, type ReactNode } from 'react'
import { useSWRConfig } from 'swr'
import { ApiError } from '../api/client'
import { STATE_KEY } from '../api/hooks'
import { BrandMark, TelegramIcon } from './icons'
import './fallback.css'

const SUPPORT = 'https://t.me/e1337_u53r'

function Fallback({
  icon,
  title,
  children,
  action,
  details,
  full = false,
}: {
  icon: ReactNode
  title: string
  children: ReactNode
  action: ReactNode
  details?: string
  /** The whole window, with no cabinet around it to orient by. */
  full?: boolean
}) {
  return (
    <main className={full ? 'fallback fallback--full' : 'fallback'} role="alert">
      {full ? (
        <span className="brand fallback__brand">
          <BrandMark />
          <span className="brand__name">gethire</span>
        </span>
      ) : null}
      <div className="fallback__body">
        <span className="fallback__icon" aria-hidden="true">
          {icon}
        </span>
        <h1 className="fallback__title">{title}</h1>
        <p className="fallback__text">{children}</p>
        <div className="fallback__actions">
          {action}
          <a
            className="btn btn--ghost"
            href={SUPPORT}
            target="_blank"
            rel="noopener noreferrer"
          >
            <TelegramIcon />
            Написать в поддержку
          </a>
        </div>
        {details ? (
          <details className="fallback__details">
            <summary>Подробности для поддержки</summary>
            <pre>{details}</pre>
          </details>
        ) : null}
      </div>
    </main>
  )
}

/** A network failure or a 5xx: the server, not the request, is the problem. */
function isServerDown(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500
  // fetch rejects with a TypeError when the connection itself fails.
  return error instanceof TypeError
}

/** The cabinet's data did not load. Shown in place of the page, beside the
 *  sidebar, so the user still sees where they are. */
export function StateError({ error }: { error: unknown }) {
  const { mutate } = useSWRConfig()
  const [retrying, setRetrying] = useState(false)
  const message = error instanceof Error ? error.message : String(error)

  async function retry() {
    setRetrying(true)
    try {
      await mutate(STATE_KEY)
    } finally {
      setRetrying(false)
    }
  }

  const action = (
    <button type="button" className="btn" onClick={retry} disabled={retrying}>
      {retrying ? 'Пробую…' : 'Повторить'}
    </button>
  )

  return isServerDown(error) ? (
    <Fallback
      icon={<ServerIcon />}
      title="Сервер не отвечает"
      action={action}
      details={message}
    >
      Чаще всего это минута-другая — например, пока выкатывается обновление. Ваши данные на
      месте, ничего не потерялось.
    </Fallback>
  ) : (
    <Fallback icon={<AlertIcon />} title="Не удалось загрузить данные" action={action}>
      {message}
    </Fallback>
  )
}

/** The page itself threw while rendering. Takes the whole window: the
 *  cabinet around it may be what broke. */
export function CrashScreen({ error }: { error: Error }) {
  return (
    <Fallback
      full
      icon={<AlertIcon />}
      title="Что-то пошло не так"
      details={error.stack || error.message}
      action={
        <button type="button" className="btn" onClick={() => location.reload()}>
          Перезагрузить страницу
        </button>
      }
    >
      Страница не смогла отрисоваться. Ваши данные на сервере не пострадали — обычно
      помогает перезагрузка.
    </Fallback>
  )
}

const ServerIcon = () => (
  <svg
    width="26"
    height="26"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </svg>
)

const AlertIcon = () => (
  <svg
    width="26"
    height="26"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M10.3 4.2 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z" />
    <path d="M12 9.5v4M12 17h.01" />
  </svg>
)
