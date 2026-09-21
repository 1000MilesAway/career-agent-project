/** One vacancy, in a list.
 *
 * Search and the shortlist render the same row with different actions: search
 * decides what is worth keeping, the shortlist does the work on it. Keeping
 * the materials and the tracker on both made one screen answer two questions
 * at once — see docs/decisions.md, 2026-09-19.
 */

import { memo, type ReactNode } from 'react'
import type { FoundVacancy, TriageBucket } from '../api/types'
import { Score } from './ui'
import './vacancy.css'

export const BUCKET_TITLES: Record<TriageBucket, string> = {
  worth: 'стоит посмотреть',
  maybe: 'может быть',
  skip: 'мимо',
}

/** Stages where a free filter or triage threw the posting out. */
export const DROPPED_STAGES = new Set(['screened_out', 'triaged_out'])

function VacancyRowInner({
  vacancy,
  actions,
}: {
  vacancy: FoundVacancy
  actions: ReactNode
}) {
  const dropped = DROPPED_STAGES.has(vacancy.stage)
  const dimmed = dropped || vacancy.triage?.bucket === 'skip'

  return (
    <article className={dimmed ? 'vrow vrow--dim' : 'vrow'}>
      <div className="vrow__main">
        <h3 className="vrow__title">
          {vacancy.url ? (
            <a href={vacancy.url} target="_blank" rel="noopener noreferrer">
              {vacancy.title}
            </a>
          ) : (
            vacancy.title
          )}
        </h3>

        {/* A drop without its reason is exactly the silent rejection the
            pipeline forbids, so the reason rides along with the row. */}
        {dropped ? (
          <p className="vrow__meta">
            <span className="chip chip--neg">отсеяно</span>{' '}
            {vacancy.screen_reason || 'без причины'}
          </p>
        ) : null}

        <p className="vrow__meta">
          {vacancy.source === 'telegram' ? 'Telegram · ' : ''}
          {vacancy.company || 'компания не указана'} ·{' '}
          {vacancy.qualification || 'грейд не указан'}
          {vacancy.remote ? ' · удалённо' : ''} ·{' '}
          {vacancy.salary_text || 'зарплата не указана'}
        </p>

        {vacancy.triage && !dropped ? (
          <p className="vrow__meta">
            <span className={`tag tag--${vacancy.triage.bucket}`}>
              {BUCKET_TITLES[vacancy.triage.bucket] ?? vacancy.triage.bucket}
            </span>{' '}
            {vacancy.triage.reason}
          </p>
        ) : null}
      </div>

      <div className="vrow__score">
        <Score score={vacancy.score} verdict={vacancy.verdict} />
      </div>

      <div className="vrow__acts">{actions}</div>
    </article>
  )
}

// A scan can put a hundred rows on screen, and every mutation revalidates the
// whole state payload. Without memo each one re-renders on every keystroke in
// the salary field above the list.
export const VacancyRow = memo(VacancyRowInner)
