/** Small presentational pieces used by more than one page. */

import type { ReactNode } from 'react'
import type { Verdict } from '../api/types'

export const VERDICT_TITLES: Record<Verdict, string> = {
  apply: 'откликаться',
  apply_with_caveats: 'с оговорками',
  skip: 'не тратить время',
}

export function Card({
  title,
  actions,
  children,
}: {
  title?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card__head">
          {title ? <h2 className="card__title">{title}</h2> : <span />}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

/** The title of a page, the one line under it, and the page's own actions. */
export function PageHead({
  title,
  lede,
  actions,
}: {
  title: string
  lede?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page__head">
      <div>
        <h1 className="page__title">{title}</h1>
        {lede ? <p className="page__lede">{lede}</p> : null}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </header>
  )
}

/** An empty list that says what to do next rather than just "nothing". */
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h2 className="empty__title">{title}</h2>
      {children}
    </div>
  )
}

/** A failure the user can act on, never a blank area that reads as "empty". */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <p className="err" role="alert">
      {message}
    </p>
  )
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

/** One decimal always: a bare "4" reads as a rounded score next to a "4.3". */
export function formatScore(score: number): string {
  return score.toFixed(1)
}

export function Score({
  score,
  verdict,
}: {
  score: number | null
  verdict: Verdict | null
}) {
  if (score == null) return null
  const tone = verdict === 'apply' ? 'good' : verdict === 'skip' ? 'bad' : 'mid'
  return (
    <span
      className={`score score--${tone}`}
      title={verdict ? `Оценка из 5 — ${VERDICT_TITLES[verdict]}` : 'Оценка из 5'}
    >
      {formatScore(score)}
    </span>
  )
}

export function Chips({
  items,
  negative = false,
}: {
  items?: string[]
  negative?: boolean
}) {
  if (!items?.length) return <span className="muted">пусто</span>
  return (
    <div className="chips">
      {items.map((item) => (
        <span key={item} className={negative ? 'chip chip--neg' : 'chip'}>
          {item}
        </span>
      ))}
    </div>
  )
}

/** Money, always with the same number of decimals and the same unit. */
export const rub = (value: number, digits = 2): string => `${value.toFixed(digits)} ₽`

/** NBSP, so a wrapped line never splits "250 000" in half. */
export const formatSalary = (value: number): string =>
  value.toLocaleString('ru-RU').replace(/\s/g, ' ')
