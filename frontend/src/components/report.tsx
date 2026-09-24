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
import { formatScore, VERDICT_TITLES } from './ui'

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

export function ReportView({ report }: { report: Report }) {
  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 12 }}>
        <span style={{ fontSize: 34, fontWeight: 600, lineHeight: 1 }}>{formatScore(report.score)}</span>
        <span className={`pill pill--${report.verdict}`}>
          {VERDICT_TITLES[report.verdict] ?? report.verdict}
        </span>
      </div>

      <p style={{ marginTop: 0 }}>{report.role_summary}</p>

      {report.requirements?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 18 }}>
            Требования
          </h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>требование</th>
                  <th>вес</th>
                  <th>совпадение</th>
                  <th>обоснование</th>
                </tr>
              </thead>
              <tbody>
                {report.requirements.map((requirement, index) => (
                  <tr key={`${requirement.text}-${index}`}>
                    <td>{requirement.text}</td>
                    <td className="muted">{weightOf(requirement)}</td>
                    <td className={`m-${requirement.match}`}>
                      {MATCH_TITLES[requirement.match] ?? requirement.match}
                    </td>
                    <td>
                      {requirement.evidence}
                      {requirement.gap_advice ? (
                        <>
                          <br />
                          <span className="muted">{requirement.gap_advice}</span>
                        </>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {report.red_flags?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 18 }}>
            Красные флаги
          </h3>
          <ul>
            {report.red_flags.map((flag, index) => (
              <li key={index}>{flag}</li>
            ))}
          </ul>
        </>
      ) : null}

      {report.salary ? (
        <>
          <h3 className="card__title" style={{ marginTop: 18 }}>
            Зарплата
          </h3>
          <p className="muted" style={{ margin: '0 0 4px' }}>
            Вилка: {report.salary.vacancy_range ?? 'не указана'} · Рынок:{' '}
            {report.salary.market_estimate ?? '?'}
          </p>
          <p style={{ margin: 0 }}>{report.salary.comment}</p>
        </>
      ) : null}

      {report.questions_to_employer?.length ? (
        <>
          <h3 className="card__title" style={{ marginTop: 18 }}>
            Вопросы работодателю
          </h3>
          <ul>
            {report.questions_to_employer.map((question, index) => (
              <li key={index}>{question}</li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}

export function CoverLetterView({ value }: { value: CoverLetter }) {
  return (
    <div>
      {value.angles?.length ? (
        <p className="muted" style={{ marginTop: 0 }}>
          Углы: {value.angles.join(' · ')}
        </p>
      ) : null}
      <pre>{value.letter}</pre>
    </div>
  )
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
      {value.ats_keywords?.length ? (
        <p className="muted">Ключевые слова: {value.ats_keywords.join(', ')}</p>
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
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>вопрос</th>
                <th>почему спросят</th>
                <th>на что опереться</th>
              </tr>
            </thead>
            <tbody>
              {value.questions.map((item, index) => (
                <tr key={index}>
                  <td>{item.question}</td>
                  <td className="muted">{item.why}</td>
                  <td>{item.answer_hint}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
