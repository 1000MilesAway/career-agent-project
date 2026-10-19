/** Конверсия: how far the people who signed up got.
 *
 * A cohort funnel — the period picks who (signed up in it), the stages count
 * what they ever did, and each stage requires the ones before it, so the bars
 * only shrink and the loss between two stages is a plain subtraction. Weekly
 * cohorts below show whether a change to the product moved the numbers.
 *
 * Period and channel live in the URL, like the other dashboard pages.
 */

import { useSearchParams } from 'react-router-dom'
import { useAdminFunnel } from '../api/hooks'
import type { AdminFunnel, FunnelChannel, FunnelExtra, FunnelStage } from '../api/types'
import { Card, Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { formatCount, formatDate, formatDay, formatPct } from './adminFormat'
import './admin.css'

const STAGES: Record<FunnelStage, { title: string; hint: string }> = {
  signed_up: { title: 'Регистрация', hint: 'учётка создана' },
  can_log_in: { title: 'Вход открыт', hint: 'подтвердили почту или вошли через Telegram' },
  resume: { title: 'Резюме', hint: 'загрузили резюме' },
  scan: { title: 'Поиск', hint: 'запустили поиск вакансий' },
  evaluation: { title: 'Оценка', hint: 'оценили хотя бы одну вакансию' },
  applied: { title: 'Отклик', hint: 'отметили отклик в избранном' },
}

const EXTRAS: Record<FunnelExtra, string> = {
  triage: 'Умный отбор',
  saved: 'Добавили в избранное',
  artifact: 'Документы к отклику',
  paid: 'Подписка',
}

const PERIODS = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
  { days: 0, label: 'Всё время' },
]
const DEFAULT_DAYS = 30

const CHANNELS: { value: FunnelChannel; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'email', label: 'Почта' },
  { value: 'telegram', label: 'Telegram' },
]

export function AdminFunnelPage() {
  const [params, setParams] = useSearchParams()
  const askedDays = params.get('days')
  const days = PERIODS.some((p) => String(p.days) === askedDays)
    ? Number(askedDays)
    : DEFAULT_DAYS
  const askedChannel = params.get('channel')
  const channel = CHANNELS.find((c) => c.value === askedChannel)?.value ?? 'all'
  const { data, error, isLoading, isValidating } = useAdminFunnel(days, channel)

  function update(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  return (
    <div className="page page--wide">
      <PageHead
        title="Конверсия"
        lede={
          data?.since
            ? `Учётки, зарегистрированные с ${formatDate(data.since)}, и как далеко они дошли. Админы не учитываются.`
            : 'Все учётки и как далеко они дошли. Админы не учитываются.'
        }
      />

      <div className="admin-toolbar">
        <div className="seg" role="group" aria-label="Период регистрации">
          {PERIODS.map((period) => (
            <button
              key={period.days}
              type="button"
              className="seg__item"
              aria-pressed={days === period.days}
              onClick={() =>
                update('days', period.days === DEFAULT_DAYS ? null : String(period.days))
              }
            >
              {period.label}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Как зарегистрировались">
          {CHANNELS.map((option) => (
            <button
              key={option.value}
              type="button"
              className="seg__item"
              aria-pressed={channel === option.value}
              onClick={() =>
                update('channel', option.value === 'all' ? null : option.value)
              }
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <ErrorNote error={error} />

      {isLoading && !data ? (
        <Skeleton rows={6} />
      ) : data && data.counts[0] === 0 ? (
        <Empty title="За этот период никто не зарегистрировался">
          <p className="muted">Выберите период подлиннее или другой канал.</p>
        </Empty>
      ) : data ? (
        <div className={isValidating ? 'admin-body admin-body--stale' : 'admin-body'}>
          <Card title="Воронка">
            <FunnelBars data={data} />
          </Card>
          <Extras data={data} />
          <Sources data={data} />
          <Cohorts data={data} />
        </div>
      ) : null}
    </div>
  )
}

/** One bar per stage, its width the share of sign-ups, with the loss to the
 *  next stage written between them — the number this page exists for. */
function FunnelBars({ data }: { data: AdminFunnel }) {
  const start = data.counts[0]
  return (
    <ol className="funnel">
      {data.stages.map((stage, i) => {
        const count = data.counts[i]
        const prev = i > 0 ? data.counts[i - 1] : null
        return (
          <li key={stage} className="funnel__step">
            {prev != null && prev > count ? (
              <div className="funnel__loss">
                −{formatCount(prev - count)} · не дошли {formatPct(prev - count, prev)}
              </div>
            ) : null}
            <div className="funnel__row">
              <div className="funnel__label">
                <b>{STAGES[stage].title}</b>
                <span className="muted">{STAGES[stage].hint}</span>
              </div>
              <div className="funnel__track" aria-hidden="true">
                <span style={{ width: start > 0 ? `${(count / start) * 100}%` : 0 }} />
              </div>
              <div className="funnel__nums num">
                <b>{formatCount(count)}</b>
                <span className="muted">{formatPct(count, start)}</span>
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Extras({ data }: { data: AdminFunnel }) {
  const start = data.counts[0]
  return (
    <div className="tiles">
      {(Object.keys(EXTRAS) as FunnelExtra[]).map((key) => (
        <div key={key} className="tile">
          <div className="tile__label">{EXTRAS[key]}</div>
          <div className="tile__value num">{formatCount(data.extras[key])}</div>
          <div className="tile__sub">
            {formatPct(data.extras[key], start)} от регистраций
          </div>
        </div>
      ))}
    </div>
  )
}

/** Weeks of sign-ups as rows. */
function Cohorts({ data }: { data: AdminFunnel }) {
  if (data.cohorts.length < 2) return null
  return (
    <Card title="По неделям регистрации">
      <ShareTable
        data={data}
        head="Неделя"
        rows={data.cohorts.map((cohort) => ({
          key: cohort.week,
          label: `с ${formatDay(cohort.week, { day: 'numeric', month: 'short' })}`,
          stages: cohort.stages,
        }))}
      />
    </Card>
  )
}

/** One row per `?ref=` the accounts signed up with. Shown from the first
 *  labelled account: a single unlabelled row says nothing. */
function Sources({ data }: { data: AdminFunnel }) {
  if (!data.by_ref.some((row) => row.ref !== null)) return null
  return (
    <Card title="По источникам">
      <ShareTable
        data={data}
        head="Источник"
        rows={data.by_ref.map((row) => ({
          key: row.ref ?? '',
          label: row.ref ?? 'без метки',
          stages: row.stages,
        }))}
      />
    </Card>
  )
}

/** Groups of sign-ups as rows, the share of each group that reached a stage
 *  as cells. The tint deepens with the share, the number stays in ink. */
function ShareTable({
  data,
  head,
  rows,
}: {
  data: AdminFunnel
  head: string
  rows: { key: string; label: string; stages: number[] }[]
}) {
  return (
    <div className="table-wrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th scope="col">{head}</th>
            {data.stages.map((stage) => (
              <th key={stage} scope="col" className="table__num">
                {STAGES[stage].title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const start = row.stages[0]
            return (
              <tr key={row.key}>
                <td className="table__date">{row.label}</td>
                {row.stages.map((count, i) =>
                  i === 0 ? (
                    <td key={i} className="table__num">
                      {formatCount(count)}
                    </td>
                  ) : (
                    <td
                      key={i}
                      className="table__num cohort-cell"
                      title={`${formatCount(count)} из ${formatCount(start)}`}
                      style={{
                        ['--share' as string]: `${start > 0 ? Math.round((count / start) * 100) : 0}%`,
                      }}
                    >
                      {formatPct(count, start)}
                    </td>
                  ),
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
