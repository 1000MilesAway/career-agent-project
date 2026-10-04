/** Траты на модель: what the DeepSeek key paid for, by day, step and user.
 *
 * Roubles first, because the owner's books are in roubles; dollars and
 * tokens are a hover away. The period is in the URL, so a reload keeps it.
 *
 * The chart is plain HTML bars, not a charting library: one series of daily
 * sums, and a dependency to draw it would outweigh the page.
 */

import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAdminSpend } from '../api/hooks'
import type { AdminSpend, SpendDay, SpendTotals } from '../api/types'
import { Card, Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import {
  formatCompact,
  formatCount,
  formatDate,
  formatDay,
  formatPct,
  formatRub,
  formatUsd,
  stepTitle,
} from './adminFormat'
import './admin.css'

const PERIODS = [7, 30, 90] as const
const DEFAULT_DAYS = 30

export function AdminSpendPage() {
  const [params, setParams] = useSearchParams()
  const asked = Number(params.get('days'))
  const days = (PERIODS as readonly number[]).includes(asked) ? asked : DEFAULT_DAYS
  const { data, error, isLoading, isValidating } = useAdminSpend(days)

  return (
    <div className="page page--wide">
      <PageHead
        title="Траты на модель"
        lede={data ? <JournalNote data={data} /> : null}
        actions={
          <div className="seg" role="group" aria-label="Период">
            {PERIODS.map((period) => (
              <button
                key={period}
                type="button"
                className="seg__item"
                aria-pressed={days === period}
                onClick={() =>
                  setParams(period === DEFAULT_DAYS ? {} : { days: String(period) }, {
                    replace: true,
                  })
                }
              >
                {period} дней
              </button>
            ))}
          </div>
        }
      />

      <ErrorNote error={error} />

      {isLoading && !data ? (
        <Skeleton rows={6} />
      ) : data ? (
        <div className={isValidating ? 'admin-body admin-body--stale' : 'admin-body'}>
          <Tiles totals={data.totals} />
          {data.totals.calls === 0 ? (
            <Empty title="За этот период вызовов не было">
              <p className="muted">
                Журнал пополняется с каждым платным шагом пользователей.
              </p>
            </Empty>
          ) : (
            <>
              <Card title="По дням, ₽">
                <DailyChart daily={data.daily} />
                <DailyTable daily={data.daily} />
              </Card>
              <div className="admin-grid">
                <Card title="По шагам">
                  <GroupTable
                    head="Шаг"
                    rows={data.by_step.map((g) => ({ ...g, label: stepTitle(g.key) }))}
                    total={data.totals.rub}
                  />
                </Card>
                <Card title="По моделям">
                  <GroupTable
                    head="Модель"
                    rows={data.by_model.map((g) => ({ ...g, label: g.key }))}
                    total={data.totals.rub}
                  />
                </Card>
              </div>
              <Card title="Кто больше всех тратит">
                <GroupTable
                  head="Пользователь"
                  rows={data.top_users.map((u) => ({ ...u, key: u.user_id ?? 'deleted' }))}
                  total={data.totals.rub}
                />
              </Card>
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

/** Where the numbers begin. Spend from before the journal exists only as the
 * lifetime sum on each account, and a chart that silently starts mid-period
 * would read as "nothing was spent before". */
function JournalNote({ data }: { data: AdminSpend }) {
  const lifetime = `Всего по учёткам за всё время: ${formatRub(data.lifetime_rub)}.`
  if (!data.journal_since) return <>Журнал вызовов пока пуст. {lifetime}</>
  if (data.journal_since > data.since) {
    return (
      <>
        Журнал вызовов ведётся с {formatDate(data.journal_since)} — раньше по дням не
        разложить. {lifetime}
      </>
    )
  }
  return <>Дни — по Москве, как лимиты. {lifetime}</>
}

function Tiles({ totals }: { totals: SpendTotals }) {
  const tokensIn = totals.cache_hit + totals.cache_miss
  // The peak surcharge in roubles: the share of the real price above the
  // off-peak one. The owner carries it; quotas are charged off-peak.
  const surcharge = totals.usd > 0 ? totals.rub * (1 - totals.offpeak_usd / totals.usd) : 0
  return (
    <div className="tiles">
      <Tile
        label="Себестоимость"
        value={formatRub(totals.rub)}
        sub={formatUsd(totals.usd)}
      />
      <Tile
        label="Вызовов"
        value={formatCount(totals.calls)}
        sub={
          totals.errors ? (
            <span className="tile__bad">ошибок: {formatCount(totals.errors)}</span>
          ) : (
            'без ошибок'
          )
        }
      />
      <Tile
        label="Средний вызов"
        value={totals.calls ? formatRub(totals.rub / totals.calls) : '—'}
      />
      <Tile
        label="Токены на входе"
        value={formatCompact(tokensIn)}
        title={formatCount(tokensIn)}
        sub={`из кэша ${formatPct(totals.cache_hit, tokensIn)}`}
      />
      <Tile
        label="Токены на выходе"
        value={formatCompact(totals.output)}
        title={formatCount(totals.output)}
      />
      <Tile label="Надбавка пика" value={formatRub(surcharge)} sub="не входит в лимиты" />
    </div>
  )
}

function Tile({
  label,
  value,
  sub,
  title,
}: {
  label: string
  value: string
  sub?: ReactNode
  title?: string
}) {
  return (
    <div className="tile">
      <div className="tile__label">{label}</div>
      <div className="tile__value num" title={title}>
        {value}
      </div>
      {sub ? <div className="tile__sub">{sub}</div> : null}
    </div>
  )
}

/** A "nice" ceiling for the axis: 1, 2 or 5 times a power of ten. */
function niceMax(value: number): number {
  if (value <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  return ([1, 2, 5, 10].find((step) => step * power >= value) ?? 10) * power
}

function DailyChart({ daily }: { daily: SpendDay[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const top = niceMax(Math.max(...daily.map((d) => d.rub)))
  const ticks = [top, top / 2, 0]
  // Every day on a week, about five labels on longer periods: six already
  // collide on a phone. Counted back from today, so today always has one.
  const labelEvery = daily.length <= 10 ? 1 : Math.ceil(daily.length / 5)
  const shown = hover == null ? null : daily[hover]
  // «28.09» rather than «28 сент.» once labels are spaced out: a phone fits
  // five of the short ones and not five of the long.
  const labelFormat: Intl.DateTimeFormatOptions =
    labelEvery === 1
      ? { day: 'numeric', month: 'short' }
      : { day: '2-digit', month: '2-digit' }

  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <div className="chart__plot">
        <div className="chart__grid" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick}>
              <span className="chart__tick">{formatRub(tick)}</span>
            </span>
          ))}
        </div>
        <div className="chart__bars" role="group" aria-label="Себестоимость по дням">
          {daily.map((day, i) => (
            <button
              key={day.day}
              type="button"
              className={hover === i ? 'chart__col chart__col--on' : 'chart__col'}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              aria-label={`${formatDay(day.day, { day: 'numeric', month: 'long' })}: ${formatRub(day.rub)}, вызовов ${day.calls}`}
            >
              <span
                className="chart__bar"
                style={{ height: `${(day.rub / top) * 100}%` }}
                data-empty={day.rub === 0 || undefined}
              />
            </button>
          ))}
        </div>
        {shown && hover != null ? (
          <div
            className="chart__tip"
            role="status"
            style={{ left: `${((hover + 0.5) / daily.length) * 100}%` }}
            data-edge={
              hover < daily.length / 5
                ? 'start'
                : hover > (daily.length * 4) / 5
                  ? 'end'
                  : undefined
            }
          >
            <b>
              {formatDay(shown.day, { weekday: 'short', day: 'numeric', month: 'long' })}
            </b>
            <span className="num">
              {formatRub(shown.rub)} · {formatUsd(shown.usd)}
            </span>
            <span className="num muted">
              вызовов {formatCount(shown.calls)}
              {shown.errors ? `, ошибок ${shown.errors}` : ''}
            </span>
            <span className="num muted">
              вход {formatCompact(shown.cache_hit + shown.cache_miss)} · выход{' '}
              {formatCompact(shown.output)}
            </span>
          </div>
        ) : null}
        <div className="chart__labels" aria-hidden="true">
          {daily.map((day, i) => (
            <span key={day.day}>
              {(daily.length - 1 - i) % labelEvery === 0
                ? formatDay(day.day, labelFormat)
                : ''}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

/** The chart's numbers as a table, for a screen reader and for copying. */
function DailyTable({ daily }: { daily: SpendDay[] }) {
  return (
    <details className="admin-details">
      <summary>Таблица по дням</summary>
      <div className="table-wrap">
        <table className="table table--compact">
          <thead>
            <tr>
              <th scope="col">День</th>
              <th scope="col" className="table__num">
                Вызовов
              </th>
              <th scope="col" className="table__num">
                Вход
              </th>
              <th scope="col" className="table__num">
                Выход
              </th>
              <th scope="col" className="table__num">
                $
              </th>
              <th scope="col" className="table__num">
                ₽
              </th>
            </tr>
          </thead>
          <tbody>
            {[...daily].reverse().map((day) => (
              <tr key={day.day}>
                <td>
                  {formatDay(day.day, { weekday: 'short', day: 'numeric', month: 'short' })}
                </td>
                <td className="table__num">{formatCount(day.calls)}</td>
                <td className="table__num">
                  {formatCount(day.cache_hit + day.cache_miss)}
                </td>
                <td className="table__num">{formatCount(day.output)}</td>
                <td className="table__num">{formatUsd(day.usd)}</td>
                <td className="table__num">{formatRub(day.rub)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

interface GroupRow extends SpendTotals {
  key: string
  label: string
}

/** Who or what the money went to, with each row's share drawn under it. */
function GroupTable({
  head,
  rows,
  total,
}: {
  head: string
  rows: GroupRow[]
  total: number
}) {
  return (
    <div className="table-wrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th scope="col">{head}</th>
            <th scope="col" className="table__num">
              Вызовов
            </th>
            <th scope="col" className="table__num" title="Токены на входе, доля из кэша">
              Вход
            </th>
            <th scope="col" className="table__num">
              Выход
            </th>
            <th scope="col" className="table__num">
              ₽
            </th>
            <th scope="col" className="table__num">
              Доля
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const tokensIn = row.cache_hit + row.cache_miss
            return (
              <tr key={row.key}>
                <td className="table__label">{row.label}</td>
                <td className="table__num">
                  {formatCount(row.calls)}
                  {row.errors ? <span className="tile__bad"> ({row.errors})</span> : null}
                </td>
                <td
                  className="table__num"
                  title={`из кэша ${formatPct(row.cache_hit, tokensIn)}`}
                >
                  {formatCompact(tokensIn)}
                </td>
                <td className="table__num">{formatCompact(row.output)}</td>
                <td className="table__num">{formatRub(row.rub)}</td>
                <td className="table__num table__share">
                  <span>{formatPct(row.rub, total)}</span>
                  <span className="share" aria-hidden="true">
                    <span
                      style={{ width: total > 0 ? `${(row.rub / total) * 100}%` : 0 }}
                    />
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
