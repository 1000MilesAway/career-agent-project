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
  mismatch,
}: {
  vacancy: FoundVacancy
  actions: ReactNode
  /** Why the row fails the conditions above the list, when it is shown
   *  anyway under «не под условия». */
  mismatch?: string | null
  /** A second line of controls under the row, for the shortlist's materials. */
  footer?: ReactNode
}) {
  const dropped = DROPPED_STAGES.has(vacancy.stage)
  const dimmed =
    dropped ||
    Boolean(mismatch) ||
    bucketOf(vacancy) === 'skip' ||
    Boolean(vacancy.closed || vacancy.stale || vacancy.hidden)
  const sourceLabel = SOURCE_LABELS[vacancy.source]

  const meta = [
    vacancy.qualification || 'грейд не указан',
    vacancy.remote ? 'удалённо' : null,
    vacancy.accredited ? 'ИТ-аккредитация' : null,
    sourceLabel ?? null,
  ].filter(Boolean)

  // Closed is marked, not hidden: what was bought for it stays readable. An
  // outdated score still reads against the old profile, so it is marked too.
  const flags = [
    mismatch && !dropped ? { label: 'не под условия', why: mismatch } : null,
    vacancy.closed
      ? {
          label: 'закрыта',
          why: vacancy.closed.reason || 'источник сообщил, что вакансия закрыта',
        }
      : null,
    vacancy.stale ? { label: 'давно', why: vacancy.stale } : null,
    vacancy.hidden ? { label: 'скрыта', why: 'вы скрыли эту вакансию' } : null,
    vacancy.outdated
      ? { label: 'прежнее резюме', why: 'оценка сделана до правки резюме' }
      : null,
  ].filter((flag) => flag !== null)
  const verdict = vacancy.triage && !dropped ? vacancy.triage : null

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

        {/* Marks of the row's state in one line. The why is on hover: written
            out under each one, four marks made the card read like a log. */}
        {/* The verdict leads the row: it is the first thing to read about a
            vacancy, and its reason follows as plain text below. */}
        {verdict || flags.length ? (
          <p className="vrow__flags">
            {verdict ? (
              <span className={`chip chip--verdict chip--${verdict.bucket}`}>
                {BUCKET_TITLES[verdict.bucket] ?? verdict.bucket}
              </span>
            ) : null}
            {flags.map((flag) => (
              <span key={flag.label} className="chip chip--flag" title={flag.why}>
                {flag.label}
              </span>
            ))}
          </p>
        ) : null}

        {verdict?.reason ? <p className="vrow__note">{verdict.reason}</p> : null}

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
