/** A round "?" in the corner that opens a way to reach a person.
 *
 * On every screen, the login included: a question about the service comes up
 * before an account as often as after. A link to the owner's Telegram rather
 * than a form — there is no support desk behind it, and a form would promise
 * one.
 */

import { useState, type FocusEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { TelegramIcon } from './icons'
import './support.css'

const CONTACT = 'https://t.me/e1337_u53r'

export function SupportBubble() {
  const [open, setOpen] = useState(false)
  const { pathname } = useLocation()

  // Focus leaving the bubble closes the card: a click elsewhere or Tab away.
  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
  }

  return (
    <div
      // The chat keeps its composer at the bottom; the bubble sits above it.
      className={pathname === '/chat' ? 'support support--raised' : 'support'}
      onBlur={onBlur}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false)
      }}
    >
      {open ? (
        <div className="support__card" role="dialog" aria-label="Связаться с нами">
          <p className="support__title">Вопрос или идея?</p>
          <p className="support__text">Напишите — отвечает живой человек.</p>
          <a
            className="btn btn--small support__link"
            href={CONTACT}
            target="_blank"
            rel="noopener noreferrer"
          >
            <TelegramIcon />
            Написать в Telegram
          </a>
        </div>
      ) : null}
      <button
        type="button"
        className="support__button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label="Поддержка"
        title="Поддержка"
      >
        ?
      </button>
    </div>
  )
}
