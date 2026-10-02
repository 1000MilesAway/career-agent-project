/** The legal documents, open to anyone: no login, no state.
 *
 * Before the token check in App, like /verify — a policy that can be read only
 * after agreeing to it is no policy.
 */

import { useEffect, type ReactNode } from 'react'
import { NavLink, useParams } from 'react-router-dom'
import { Consent, Privacy, Terms, type DocId } from './docs'
import './legal.css'

const DOCS: Record<DocId, { title: string; body: () => ReactNode }> = {
  privacy: { title: 'Политика обработки персональных данных', body: Privacy },
  consent: { title: 'Согласие на обработку персональных данных', body: Consent },
  terms: { title: 'Пользовательское соглашение', body: Terms },
}

const ORDER: DocId[] = ['privacy', 'consent', 'terms']

export function LegalPage() {
  const { doc } = useParams()
  const id: DocId = ORDER.includes(doc as DocId) ? (doc as DocId) : 'privacy'
  const { title, body: Body } = DOCS[id]

  // The pages share one scroll: switching documents should start at the top.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [id])

  return (
    <div className="legal">
      <header className="legal__head">
        <a className="brand legal__home" href="#/" title="На главную">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">career-agent</span>
        </a>
        <nav className="legal__nav" aria-label="Документы">
          {ORDER.map((key) => (
            <NavLink
              key={key}
              to={`/legal/${key}`}
              className={({ isActive }) =>
                isActive ? 'legal__tab legal__tab--on' : 'legal__tab'
              }
            >
              {SHORT[key]}
            </NavLink>
          ))}
        </nav>
      </header>
      <article className="legal__doc">
        <h1>{title}</h1>
        <Body />
      </article>
    </div>
  )
}

const SHORT: Record<DocId, string> = {
  privacy: 'Политика',
  consent: 'Согласие',
  terms: 'Соглашение',
}

/** The row of links every entry point carries: landing, login, consent, profile. */
export function LegalLinks({ className = 'legal-links' }: { className?: string }) {
  return (
    <p className={className}>
      <a href="#/legal/privacy">Политика обработки ПД</a>
      <a href="#/legal/consent">Согласие</a>
      <a href="#/legal/terms">Пользовательское соглашение</a>
    </p>
  )
}
