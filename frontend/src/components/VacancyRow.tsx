/** One vacancy, in a list.
 *
 * Search and the shortlist render the same row with different actions: search
 * decides what is worth keeping, the shortlist does the work on it. Keeping
 * the materials and the tracker on both made one screen answer two questions
 * at once — see docs/decisions.md, 2026-09-19.
 *
 * The score leads the row, the way a match percentage leads a card in the job
 * trackers people already know: it is the one number a scan of the list is
 * for. Salary is set in ink rather than grey because it is the second.
 */

import { memo, type ReactNode } from 'react'
import type { FoundVacancy, TriageBucket, Verdict } from '../api/types'
import { ExternalIcon } from './icons'
import { Score } from './ui'
import './vacancy.css'

export const BUCKET_TITLES: Record<TriageBucket, string> = {
  worth: 'стоит посмотреть',
  maybe: 'может быть',
  skip: 'мимо',
}

const VERDICT_BUCKETS: Record<Verdict, TriageBucket> = {
  apply: 'worth',
  apply_with_caveats: 'maybe',
  skip: 'skip',
}

/** The bucket a row is filed under. A full evaluation outranks the cheap
 *  triage: it read the whole posting, triage only the listing. Triage never
 *  looks at an evaluated row, so without this a scored vacancy fell into
 *  «без вердикта» — the one row judged most thoroughly shown as unjudged.
 *  `null` then means only what it says: nothing has judged the row. */
export function bucketOf(row: FoundVacancy): TriageBucket | null {
  if (row.verdict) return VERDICT_BUCKETS[row.verdict]
  return row.triage?.bucket ?? null
}

/** Named in the row for every source but Habr, the default one. */
const SOURCE_LABELS: Record<string, string> = {
  telegram: 'Telegram',
  geekjob: 'geekjob',
  company: 'сайт компании',
}

/** Stages where a free filter or triage threw the posting out. */
export const DROPPED_STAGES = new Set(['screened_out', 'triaged_out'])

function VacancyRowInner({
  vacancy,
  actions,
  footer,
}: {
  vacancy: FoundVacancy
  actions: ReactNode
  /** A second line of controls under the row, for the shortlist's materials. */
  footer?: ReactNode
}) {
  const dropped = DROPPED_STAGES.has(vacancy.stage)
  const dimmed =
    dropped ||
    bucketOf(vacancy) === 'skip' ||
    Boolean(vacancy.closed || vacancy.stale || vacancy.hidden)
  const sourceLabel = SOURCE_LABELS[vacancy.source]

  const meta = [
    vacancy.qualification || 'грейд не указан',
    vacancy.remote ? 'удалённо' : null,
    vacancy.accredited ? 'ИТ-аккредитация' : null,
    sourceLabel ?? null,
  ].filter(Boolean)

  return (
    <article className={dimmed ? 'vrow vrow--dim' : 'vrow'}>
      <div className="vrow__lead">
        {vacancy.score != null ? (
          <Score score={vacancy.score} verdict={vacancy.verdict} />
        ) : (
          <span
            className={`vrow__pending dot${vacancy.triage && !dropped ? ` dot--${vacancy.triage.bucket}` : ''}`}
            aria-hidden="true"
          />
        )}
      </div>

      <div className="vrow__main">
        <h3 className="vrow__title">
          {vacancy.url ? (
            <a href={vacancy.url} target="_blank" rel="noopener noreferrer">
              {vacancy.title}
              <ExternalIcon />
            </a>
          ) : (
            vacancy.title
          )}
        </h3>

        <p className="vrow__meta">
          <span className="vrow__company">{vacancy.company || 'компания не указана'}</span>
          {/* Ink only when there is a number to read; "не указана" is an
              absence and should not shout like one. */}
          {vacancy.salary_text ? (
            <span className="vrow__salary">{vacancy.salary_text}</span>
          ) : (
            <span>зарплата не указана</span>
          )}
          {meta.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </p>

        {/* A drop without its reason is exactly the silent rejection the
            pipeline forbids, so the reason rides along with the row. */}
        {dropped ? (
          <p className="vrow__note">
            <span className="chip chip--flag">
              {vacancy.duplicate_of ? 'дубль' : 'отсеяно'}
            </span>{' '}
            {vacancy.screen_reason || 'без причины'}
          </p>
        ) : null}

        {/* Closed is marked, not hidden: what was bought for it stays
            readable, and the reason says why applying is pointless. */}
        {vacancy.closed ? (
          <p className="vrow__note">
            <span className="chip chip--flag">закрыта</span>{' '}
            {vacancy.closed.reason || 'источник сообщил, что вакансия закрыта'}
          </p>
        ) : null}

        {vacancy.stale ? (
          <p className="vrow__note">
            <span className="chip chip--flag">давно</span> {vacancy.stale}
          </p>
        ) : null}

        {vacancy.hidden ? (
          <p className="vrow__note">
            <span className="chip chip--flag">скрыта</span> вы скрыли эту вакансию
          </p>
        ) : null}

        {/* The resume changed after this score was bought: the report still
            reads against the old profile, so it is marked, not replaced. */}
        {vacancy.outdated ? (
          <p className="vrow__note">
            <span className="chip chip--flag">прежнее резюме</span> оценка сделана до правки
            резюме
          </p>
        ) : null}

        {vacancy.triage && !dropped ? (
          <p className="vrow__note">
            <span className={`tag tag--${vacancy.triage.bucket}`}>
              {BUCKET_TITLES[vacancy.triage.bucket] ?? vacancy.triage.bucket}
            </span>{' '}
            {vacancy.triage.reason}
          </p>
        ) : null}

        {footer ? <div className="vrow__footer">{footer}</div> : null}
      </div>

      <div className="vrow__acts">{actions}</div>
    </article>
  )
}

// A scan can put a hundred rows on screen, and every mutation revalidates the
// whole state payload. Without memo each one re-renders on every keystroke in
// the salary field above the list.
export const VacancyRow = memo(VacancyRowInner)
