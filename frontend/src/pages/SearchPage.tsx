/** "Поиск" — collect, screen for free, triage cheaply, evaluate.
 *
 * The order of the stages is the pipeline invariant: nothing paid runs before
 * the free filters have. The screen only ever offers the next stage, so there
 * is no button that skips one.
 */

import { useMemo, useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  clearFound,
  runTriage,
  scanVacancies,
  setHidden,
  setSaved,
  useAppState,
} from '../api/hooks'
import type { FoundVacancy, ScanResult, SourceScan } from '../api/types'
import { SalaryInput } from '../components/SalaryInput'
import { BUCKET_TITLES, DROPPED_STAGES, VacancyRow } from '../components/VacancyRow'
import { StarIcon } from '../components/icons'
import { useToast } from '../components/toast'
import { Empty, ErrorNote, PageHead, Skeleton, rub } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import { type BucketKey, loadFilters, saveFilters } from './searchFilters'
import './search.css'

/** A stable empty list, so a state payload that has not arrived yet does not
 *  hand every memo below a new array identity on each render. */
const EMPTY_ROWS: FoundVacancy[] = []

/** Where a row sits in the list. Every group but "kept" is folded under its
 *  own toggle — folded, never removed: a row that disappears without a trace
 *  is the silent drop the pipeline forbids. The user's own «Скрыть» wins over
 *  everything; a closed or stale posting outranks a filter's drop, because
 *  "it is gone" matters more than "it did not fit". */
type Group = 'kept' | 'dropped' | 'inactive' | 'hidden'

function groupOf(row: FoundVacancy): Group {
  if (row.hidden) return 'hidden'
  if (row.closed || row.stale) return 'inactive'
  if (DROPPED_STAGES.has(row.stage)) return 'dropped'
  return 'kept'
}

/** Rows rendered before "show more". A scan returns several hundred postings,
 *  and rendering all of them made the page twenty thousand pixels tall — the
 *  scroll bar stopped meaning anything. */
const PAGE_SIZE = 50

const SOURCE_TITLES: Record<string, string> = {
  habr: 'Хабр Карьера',
  company: 'Сайты компаний',
  geekjob: 'geekjob',
  telegram: 'Telegram-каналы',
}

/** One line per source. A source that failed is named with its error rather
 *  than folded into a smaller total; duplicates are named apart from the
 *  filter's drops — they are not rejected, they are already on the list. */
function sourceLine(name: string, s: SourceScan): string {
  const title = SOURCE_TITLES[name] ?? name
  if (s.error) return `${title}: не прочитан — ${s.error}`
  const dups = s.duplicates ? `, уже найдены в другом месте ${s.duplicates}` : ''
  // Dead channels and failed boards answer too, just with nothing usable.
  const problems = s.problems?.length ? `; пропущено: ${s.problems.length}` : ''
  return `${title}: собрано ${s.collected}, прошло фильтры ${s.kept ?? 0}${dups}, новых ${s.new}${problems}`
}

export function SearchPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, isBusy, panel } = useDocumentPanel()

  const [salary, setSalary] = useState<number | null>(null)
  const [anySalary, setAnySalary] = useState(true)
  const [remoteOnly, setRemoteOnly] = useState(true)
  const [includeGlobal, setIncludeGlobal] = useState(false)

  const [scanNote, setScanNote] = useState<ScanResult | null>(null)
  const [isScanning, startScan] = useTransition()
  const [isTriaging, startTriage] = useTransition()
  const [limit, setLimit] = useState(PAGE_SIZE)

  // Read once on mount; every change below writes it back, so a reload
  // shows the list the way the user left it.
  const [initialFilters] = useState(loadFilters)
  const [visibleBuckets, setVisibleBuckets] = useState<ReadonlySet<BucketKey>>(
    () => new Set(initialFilters.buckets),
  )
  const [showDropped, setShowDropped] = useState(initialFilters.showDropped)
  const [showInactive, setShowInactive] = useState(initialFilters.showInactive)
  const [showHidden, setShowHidden] = useState(initialFilters.showHidden)

  // Hoisted, not `data?.found ?? []`: a fresh [] on every render makes every
  // useMemo below recompute, which is the opposite of what they are for.
  const rows = data?.found ?? EMPTY_ROWS

  // One pass over the list: the split feeds the counters, the bucket filter
  // and the rendered rows alike. `base` keeps the payload's order, so a
  // toggled-in group lands where its rows were found rather than at the end.
  const { counts, base, scored } = useMemo(() => {
    const shownGroups: Record<Group, boolean> = {
      kept: true,
      dropped: showDropped,
      inactive: showInactive,
      hidden: showHidden,
    }
    const counts: Record<Group, number> = { kept: 0, dropped: 0, inactive: 0, hidden: 0 }
    const base: FoundVacancy[] = []
    let scored = 0
    for (const row of rows) {
      const group = groupOf(row)
      counts[group]++
      if (group === 'kept' && row.score != null) scored++
      if (shownGroups[group]) base.push(row)
    }
    return { counts, base, scored }
  }, [rows, showDropped, showInactive, showHidden])

  const bucketCounts = useMemo(() => {
    const counts = new Map<BucketKey, number>()
    for (const row of base) {
      const key = row.triage?.bucket ?? null
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [base])

  const anyJudged = useMemo(() => rows.some((row) => row.triage), [rows])

  const visible = useMemo(() => {
    if (!anyJudged) return base
    return base.filter((row) => visibleBuckets.has(row.triage?.bucket ?? null))
  }, [base, anyJudged, visibleBuckets])

  // Computed from the rendered value rather than inside a state updater: the
  // write to storage is a side effect, and updaters must stay pure.
  function toggleBucket(key: BucketKey) {
    const next = new Set(visibleBuckets)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setVisibleBuckets(next)
    setLimit(PAGE_SIZE)
    saveFilters({ buckets: [...next], showDropped, showInactive, showHidden })
  }

  function toggleGroup(group: Exclude<Group, 'kept'>) {
    const next = {
      showDropped: group === 'dropped' ? !showDropped : showDropped,
      showInactive: group === 'inactive' ? !showInactive : showInactive,
      showHidden: group === 'hidden' ? !showHidden : showHidden,
    }
    setShowDropped(next.showDropped)
    setShowInactive(next.showInactive)
    setShowHidden(next.showHidden)
    setLimit(PAGE_SIZE)
    saveFilters({ buckets: [...visibleBuckets], ...next })
  }

  function scan() {
    setScanNote(null)
    startScan(async () => {
      try {
        const result = await scanVacancies({
          source: 'all',
          salary_min: salary,
          any_salary: anySalary,
          remote_only: remoteOnly,
          include_global: includeGlobal,
        })
        setScanNote(result)
        await mutate(STATE_KEY)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function triage() {
    startTriage(async () => {
      try {
        const result = await runTriage()
        await mutate(STATE_KEY)

        if (result.note) {
          toast.info(result.note)
          return
        }
        toast.info(
          `Отсев: судили ${result.judged}, потрачено ${rub(result.cost_rub ?? 0, 3)}.`,
        )
        // A short reply leaves postings unjudged. They are reported, never
        // backfilled as "skip": an unjudged vacancy is not a rejected one.
        if (result.missing?.length) {
          toast.error(`Без вердикта осталось ${result.missing.length}.`)
        }
        // Ids the model invented. Saying so makes a broken prompt look broken.
        if (result.unknown?.length) {
          toast.error(
            `Модель вернула ${result.unknown.length} чужих id — вердикты по ним отброшены.`,
          )
        }
      } catch (e) {
        toast.error(e)
      }
    })
  }

  async function clear() {
    try {
      const result = await clearFound()
      await mutate(STATE_KEY)
      toast.info(`Убрано ${result.deleted}. Избранное не тронуто.`)
    } catch (e) {
      toast.error(e)
    }
  }

  async function toggleHidden(vacancy: FoundVacancy) {
    try {
      await setHidden(vacancy.id, !vacancy.hidden)
      await mutate(STATE_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  async function toggleSave(vacancy: FoundVacancy) {
    try {
      await setSaved(vacancy.id, !vacancy.saved)
      await mutate(STATE_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  if (error)
    return (
      <div className="page">
        <ErrorNote error={error} />
      </div>
    )
  if (isLoading || !data)
    return (
      <div className="page">
        <Skeleton rows={6} />
      </div>
    )

  if (!data.has_resume) {
    return (
      <div className="page">
        <PageHead title="Поиск" />
        <div className="card">
          <Empty title="Сначала резюме">
            <p>По нему строятся запросы и фильтры — без него искать нечем.</p>
            <a className="btn" href="#/resume">
              Загрузить резюме
            </a>
          </Empty>
        </div>
      </div>
    )
  }

  const shown = visible.slice(0, limit)

  return (
    <div className="page">
      <PageHead
        title="Поиск"
        lede={
          <>
            Хабр Карьера, сайты компаний, geekjob и Telegram-каналы — одним запуском. Лишнее
            отсеивается бесплатно, одна вакансия из разных мест показывается один раз.
            Модель считает только то, что прошло отбор.
          </>
        }
      />

      <section className="card scan" aria-label="Параметры поиска">
        <SalaryInput value={salary} anySalary={anySalary} onChange={setSalary} />

        <div className="scan__side">
          <p className="eyebrow">Условия</p>
          <div className="scan__opts">
            <label
              className="toggle"
              title="Хабр отдаёт только вакансии с указанной вилкой, если задан порог. С галочкой берём и те, где зарплата не указана — их оценят бесплатные фильтры по заголовку."
            >
              <input
                type="checkbox"
                checked={anySalary}
                onChange={(e) => setAnySalary(e.target.checked)}
              />
              и без указанной зарплаты
            </label>
            <label
              className="toggle"
              title="Вакансии, где формат работы не указан вовсе, остаются — отсеиваются только явно офисные"
            >
              <input
                type="checkbox"
                checked={remoteOnly}
                onChange={(e) => setRemoteOnly(e.target.checked)}
              />
              только удалёнка
            </label>
            <label
              className="toggle"
              title="Nebius, JetBrains, Wrike, Xsolla и другие компании с русскоязычными командами — вакансии обычно за рубежом и на английском"
            >
              <input
                type="checkbox"
                checked={includeGlobal}
                onChange={(e) => setIncludeGlobal(e.target.checked)}
              />
              и зарубежные компании
            </label>
          </div>

          <div className="scan__go">
            <button className="btn scan__btn" onClick={scan} disabled={isScanning}>
              {isScanning ? 'Ищу во всех источниках…' : 'Найти вакансии'}
            </button>
            <span className="muted">бесплатно · 1–2 минуты</span>
          </div>
        </div>

        {isScanning ? (
          <div className="scan__progress" role="status">
            <span className="scan__bar" aria-hidden="true" />
            Источники читаются параллельно, модель не вызывается.
          </div>
        ) : null}

        {scanNote ? (
          <div className="scan__result" role="status">
            <p className="scan__total">
              Найдено <b className="num">{scanNote.collected}</b>, прошло фильтры{' '}
              <b className="num">{scanNote.kept ?? 0}</b>, новых{' '}
              <b className="num">{scanNote.new}</b>
              {scanNote.duplicates
                ? `, уже найдены в другом месте ${scanNote.duplicates}`
                : ''}
              .
            </p>
            <ul className="scan__sources">
              {Object.entries(scanNote.sources ?? {}).map(([name, s]) => (
                <li key={name} className={s.error ? 'scan__source--err' : undefined}>
                  {sourceLine(name, s)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section aria-label="Найденные вакансии">
        <div className="listbar">
          <div className="listbar__title">
            <h2>Найденные</h2>
            {rows.length ? (
              <span className="muted num">
                {counts.kept} · оценено {scored}
                {data.spend.free_to_reopen
                  ? ` · ${data.spend.free_to_reopen} откроются бесплатно`
                  : ''}
              </span>
            ) : null}
          </div>
          <div className="row">
            {counts.kept ? (
              <button
                className="btn btn--ghost btn--small"
                onClick={triage}
                disabled={isTriaging}
                title="Один дешёвый прогон: разложить список на «стоит посмотреть / может быть / мимо»"
              >
                {isTriaging ? 'Отсеиваю…' : 'Отсеять моделью'}
                {isTriaging ? null : <span className="btn__price">~0.01 ₽/шт</span>}
              </button>
            ) : null}
            {rows.length ? (
              <button
                className="btn btn--quiet btn--small"
                onClick={clear}
                title="Убрать из списка всё, кроме избранного"
              >
                Очистить
              </button>
            ) : null}
          </div>
        </div>

        {rows.length ? (
          <div className="listbar listbar--filters">
            {anyJudged ? (
              <div className="seg" role="group" aria-label="Вердикт отсева">
                {(['worth', 'maybe', 'skip'] as const).map((bucket) => (
                  <button
                    key={bucket}
                    className="seg__item"
                    aria-pressed={visibleBuckets.has(bucket)}
                    onClick={() => toggleBucket(bucket)}
                  >
                    <span className={`dot dot--${bucket}`} aria-hidden="true" />
                    {BUCKET_TITLES[bucket]}
                    <span className="seg__count">{bucketCounts.get(bucket) ?? 0}</span>
                  </button>
                ))}
                {bucketCounts.get(null) ? (
                  <button
                    className="seg__item"
                    aria-pressed={visibleBuckets.has(null)}
                    onClick={() => toggleBucket(null)}
                  >
                    <span className="dot" aria-hidden="true" />
                    без вердикта
                    <span className="seg__count">{bucketCounts.get(null)}</span>
                  </button>
                ) : null}
              </div>
            ) : (
              <span className="muted">
                Список ещё не разобран — «Отсеять моделью» разложит его по вердиктам.
              </span>
            )}
            <div className="listbar__groups">
              {counts.dropped ? (
                <button
                  className="linkish listbar__dropped"
                  onClick={() => toggleGroup('dropped')}
                >
                  {showDropped ? 'убрать' : 'показать'} отсеянные ({counts.dropped})
                </button>
              ) : null}
              {counts.inactive ? (
                <button
                  className="linkish listbar__dropped"
                  onClick={() => toggleGroup('inactive')}
                  title="Закрытые у источника и слишком старые там, где источник не отвечает"
                >
                  {showInactive ? 'убрать' : 'показать'} неактуальные ({counts.inactive})
                </button>
              ) : null}
              {counts.hidden ? (
                <button
                  className="linkish listbar__dropped"
                  onClick={() => toggleGroup('hidden')}
                >
                  {showHidden ? 'убрать' : 'показать'} скрытые ({counts.hidden})
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {rows.length === 0 ? (
          <div className="card">
            <Empty title="Пока пусто">
              <p>Задайте зарплату и условия выше и запустите поиск — это бесплатно.</p>
            </Empty>
          </div>
        ) : visible.length === 0 ? (
          <div className="card">
            <Empty title="Под фильтр ничего не попало">
              <p>
                Включите другие вердикты выше или покажите отсеянные, неактуальные и
                скрытые.
              </p>
            </Empty>
          </div>
        ) : (
          <div className="vlist">
            {shown.map((vacancy) => (
              <VacancyRow
                key={vacancy.id}
                vacancy={vacancy}
                actions={
                  <>
                    {vacancy.outdated ? (
                      <button
                        className="btn btn--quiet btn--small"
                        onClick={() => openReport(vacancy, true)}
                        disabled={isBusy(`${vacancy.id}:report`)}
                        title="Оценка куплена по прежней версии резюме. Новая — около 0.17 ₽"
                      >
                        Переоценить
                      </button>
                    ) : null}
                    <button
                      className="btn btn--ghost btn--small"
                      onClick={() => openReport(vacancy)}
                      disabled={isBusy(`${vacancy.id}:report`)}
                      title={
                        vacancy.score != null
                          ? 'Отчёт уже куплен — показ бесплатный'
                          : 'Полная оценка — около 0.17 ₽'
                      }
                    >
                      {isBusy(`${vacancy.id}:report`)
                        ? 'Считаю…'
                        : vacancy.score != null
                          ? 'Отчёт'
                          : 'Оценить'}
                      {vacancy.score == null && !isBusy(`${vacancy.id}:report`) ? (
                        <span className="btn__price">0.17 ₽</span>
                      ) : null}
                    </button>
                    <button
                      className="btn btn--quiet btn--small"
                      onClick={() => toggleHidden(vacancy)}
                      title={
                        vacancy.hidden
                          ? 'Вернуть в список'
                          : 'Не интересно: убрать из списка. Вернуть можно из «скрытых»'
                      }
                    >
                      {vacancy.hidden ? 'Вернуть' : 'Скрыть'}
                    </button>
                    <button
                      className={
                        vacancy.saved
                          ? 'btn btn--quiet btn--small btn--icon star star--on'
                          : 'btn btn--quiet btn--small btn--icon star'
                      }
                      onClick={() => toggleSave(vacancy)}
                      aria-pressed={vacancy.saved}
                      aria-label={vacancy.saved ? 'В избранном' : 'В избранное'}
                      title={
                        vacancy.saved
                          ? 'Уже в избранном — нажмите, чтобы убрать'
                          : 'Отложить: материалы и статусы готовятся там'
                      }
                    >
                      <StarIcon filled={vacancy.saved} />
                    </button>
                  </>
                }
              />
            ))}
          </div>
        )}

        {visible.length > shown.length ? (
          <div className="more">
            <button
              className="btn btn--ghost"
              onClick={() => setLimit((current) => current + PAGE_SIZE)}
            >
              Показать ещё {Math.min(PAGE_SIZE, visible.length - shown.length)}
            </button>
            <span className="muted num">
              показано {shown.length} из {visible.length}
            </span>
          </div>
        ) : null}
      </section>

      {panel}
    </div>
  )
}
