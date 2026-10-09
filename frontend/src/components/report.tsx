/** The evaluation report, and the three documents built on top of it.
 *
 * These are documents a person reads and copies out, not payloads: raw JSON
 * hides the one thing worth checking, which is that nothing was invented.
 * `changes` in a tailored resume stays visible for exactly that reason.
 */

import type {
  CoverLetter,
  Importance,
  InterviewPrep,
  MatchLevel,
  Report,
  Requirement,
  TailoredResume,
} from '../api/types'
import { useRef, useState } from 'react'
import { formatScore, VERDICT_TITLES } from './ui'
import './report.css'

const MATCH_TITLES: Record<MatchLevel, string> = {
  full: 'полностью',
  partial: 'частично',
  none: 'нет',
}

/** Five bands since 2026-09-18; stored reports from before still say must/nice. */
const WEIGHT_TITLES: Record<Importance | 'must' | 'nice', string> = {
  critical: 'критично',
  high: 'высоко',
  meaningful: 'заметно',
  preferred: 'желательно',
  low_signal: 'слабый сигнал',
  must: 'обяз.',
  nice: 'жел.',
}

function weightOf(requirement: Requirement): string {
  const key = requirement.importance ?? requirement.weight
  return (key && WEIGHT_TITLES[key]) || '—'
}

const MATCH_MARKS: Record<MatchLevel, string> = { full: '✓', partial: '~', none: '✕' }

/** The report opens on the verdict, then the coverage of requirements as one
 *  bar — matched, partly, missing — the readout people know from resume
 *  scanners. The list under it is sorted by nothing but the model's order:
 *  re-sorting would hide which requirement the posting led with. */
export function ReportView({ report }: { report: Report }) {
  const requirements = report.requirements ?? []
  const tally = { full: 0, partial: 0, none: 0 }
  for (const requirement of requirements) {
    if (requirement.match in tally) tally[requirement.match]++
  }

  return (
    <div className="report">
      <header className="report__head">
        <span className={`report__score report__score--${report.verdict}`}>
          {formatScore(report.score)}
          <small>/5</small>
        </span>
        <div>
          <span className={`pill pill--${report.verdict}`}>
            {VERDICT_TITLES[report.verdict] ?? report.verdict}
          </span>
          <p className="report__summary">{report.role_summary}</p>
        </div>
      </header>

      {requirements.length ? (
        <section className="report__section">
          <div className="report__cover-head">
            <h3 className="eyebrow">Требования</h3>
            <span className="muted num">
              совпало {tally.full} · частично {tally.partial} · нет {tally.none}
            </span>
          </div>
          <div className="cover" aria-hidden="true">
            {tally.full ? (
              <span className="cover--full" style={{ flex: tally.full }} />
            ) : null}
            {tally.partial ? (
              <span className="cover--partial" style={{ flex: tally.partial }} />
            ) : null}
            {tally.none ? (
              <span className="cover--none" style={{ flex: tally.none }} />
            ) : null}
          </div>

          <ul className="reqs">
            {requirements.map((requirement, index) => (
              <li key={`${requirement.text}-${index}`} className="req">
                <span
                  className={`req__mark req__mark--${requirement.match}`}
                  title={`Совпадение: ${MATCH_TITLES[requirement.match] ?? requirement.match}`}
                >
                  {MATCH_MARKS[requirement.match] ?? '?'}
                </span>
                <div className="req__body">
                  <p className="req__text">
                    {requirement.text}
                    <span className="req__weight">{weightOf(requirement)}</span>
                  </p>
                  {requirement.evidence ? (
                    <p className="req__evidence">{requirement.evidence}</p>
                  ) : null}
                  {requirement.gap_advice ? (
                    <p className="req__advice">{requirement.gap_advice}</p>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {report.red_flags?.length ? (
        <section className="report__section">
          <h3 className="eyebrow">Красные флаги</h3>
          <ul className="flags">
            {report.red_flags.map((flag, index) => (
              <li key={index}>{flag}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {report.salary ? (
        <section className="report__section">
          <h3 className="eyebrow">Зарплата</h3>
          <div className="money">
            <div>
              <span className="money__label">Вилка в вакансии</span>
              <span className="money__value">
                {report.salary.vacancy_range ?? 'не указана'}
              </span>
            </div>
            <div>
              <span className="money__label">Рынок</span>
              <span className="money__value">{report.salary.market_estimate ?? '?'}</span>
            </div>
          </div>
          <p className="report__text">{report.salary.comment}</p>
        </section>
      ) : null}

      {report.questions_to_employer?.length ? (
        <section className="report__section">
          <h3 className="eyebrow">Вопросы работодателю</h3>
          <ol className="report__list">
            {report.questions_to_employer.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  )
}

/** The letter is set as a letter — prose on a page, not monospace in a box —
 *  because what the user checks is how it reads. It leaves the cabinet by
 *  copy and paste into a job board, so copying is one click. */
export function CoverLetterView({ value }: { value: CoverLetter }) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const letterRef = useRef<HTMLDivElement>(null)

  async function copy() {
    let ok: boolean
    try {
      await navigator.clipboard.writeText(value.letter)
      ok = true
    } catch {
      // The async API rejects without document focus, on plain http and in
      // some embedded browsers. A silent catch here left the previous letter
      // in the clipboard while the new one was on screen, and the wrong
      // letter went to an employer. Copying the selection still works there.
      ok = copySelection(letterRef.current)
    }
    // A failure is shown, never swallowed; the text stays selected, so Ctrl+C
    // is one keystroke away.
    setCopyState(ok ? 'copied' : 'failed')
    window.setTimeout(() => setCopyState('idle'), 2500)
  }

  return (
    <div className="report">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        {value.angles?.length ? (
          <div className="chips">
            {value.angles.map((angle) => (
              <span key={angle} className="chip">
                {angle}
              </span>
            ))}
          </div>
        ) : (
          <span />
        )}
        <button className="btn btn--ghost btn--small" onClick={copy}>
          {copyState === 'copied'
            ? 'Скопировано ✓'
            : copyState === 'failed'
              ? 'Не скопировалось — Ctrl+C'
              : 'Скопировать'}
        </button>
      </div>
      <div className="letter" ref={letterRef}>
        {value.letter}
      </div>
    </div>
  )
}

/** Selects the node's text and copies it the old way. The selection is left
 *  in place on purpose: if the copy fails, Ctrl+C is all that is left to do. */
export function copySelection(node: HTMLElement | null): boolean {
  const selection = window.getSelection()
  if (!node || !selection) return false
  const range = document.createRange()
  range.selectNodeContents(node)
  selection.removeAllRanges()
  selection.addRange(range)
  try {
    return document.execCommand('copy')
  } catch {
    return false
  }
}

export function TailoredResumeView({ value }: { value: TailoredResume }) {
  return (
    <div>
      {value.summary ? <p style={{ marginTop: 0 }}>{value.summary}</p> : null}

      {value.experience?.map((item, index) => (
        <div key={`${item.company}-${index}`} style={{ marginBottom: 12 }}>
          <div>
            <b>{item.role}</b> — {item.company} <span className="muted">{item.period}</span>
          </div>
          <ul style={{ margin: '4px 0' }}>
            {item.bullets?.map((bullet, i) => (
              <li key={i}>{bullet}</li>
            ))}
          </ul>
        </div>
      ))}

      {value.skills?.length ? (
        <p className="muted">Навыки: {value.skills.join(', ')}</p>
      ) : null}

      {/* What the model changed, and why. An invention shows up here. */}
      {value.changes?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 16 }}>
            Что изменено
          </h3>
          <ul>
            {value.changes.map((change, index) => (
              <li key={index}>{change}</li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}

export function InterviewPrepView({ value }: { value: InterviewPrep }) {
  return (
    <div>
      {value.questions?.length ? (
        <ol className="qa">
          {value.questions.map((item, index) => (
            <li key={index} className="qa__item">
              <p className="qa__q">{item.question}</p>
              <p className="qa__why">{item.why}</p>
              <p className="qa__hint">{item.answer_hint}</p>
            </li>
          ))}
        </ol>
      ) : null}

      {value.star_stories?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 16 }}>
            STAR-истории
          </h3>
          {value.star_stories.map((story, index) => (
            <div key={index} style={{ marginBottom: 12 }}>
              <b>{story.title}</b>
              <div className="muted">Ситуация: {story.situation}</div>
              <div className="muted">Задача: {story.task}</div>
              <div>Действия: {story.action}</div>
              <div>Результат: {story.result}</div>
            </div>
          ))}
        </>
      ) : null}

      {value.to_brush_up?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 16 }}>
            Подтянуть
          </h3>
          <ul>
            {value.to_brush_up.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}
