/** "Поиск" — collect, screen for free, triage cheaply, evaluate.
 *
 * The order of the stages is the pipeline invariant: nothing paid runs before
 * the free filters have. The screen only ever offers the next stage, so there
 * is no button that skips one.
 */

import { useMemo, useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  SCAN_JOB_KEY,
  STATE_KEY,
  clearFound,
  isActive,
  runTriage,
  hideVacancy,
  saveVacancy,
  startScan,
  useAppState,
  useScanJob,
} from '../api/hooks'
import type { FoundVacancy, SourceScan } from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SalaryInput } from '../components/SalaryInput'
import {
  BUCKET_TITLES,
  DROPPED_STAGES,
  VacancyRow,
  bucketOf,
} from '../components/VacancyRow'
import { StarIcon } from '../components/icons'
import { useToast } from '../components/toast'
import { Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import {
  type BucketKey,
  type Conditions,
  type SearchFilters,
  type SortKey,
  conditionMiss,
  loadFilters,
  saveFilters,
  sortRows,
} from './searchFilters'
import { type FrozenOrder, arrange, pendingChanges } from './frozenOrder'
import './search.css'

/** A stable empty list, so a state payload that has not arrived yet does not
 *  hand every memo below a new array identity on each render. */
const EMPTY_ROWS: FoundVacancy[] = []

/** Where a row sits in the list. Every group but "kept" is folded under its
 *  own toggle — folded, never removed: a row that disappears without a trace
 *  is the silent drop the pipeline forbids. The user's own «Скрыть» wins over
 *  everything; a closed or stale posting outranks a filter's drop, because
 *  "it is gone" matters more than "it did not fit". A row the server kept
 *  but today's conditions do not is the last fold: it carries its reason. */
type Group = 'kept' | 'dropped' | 'mismatch' | 'inactive' | 'hidden'

function groupOf(row: FoundVacancy, miss: string | null): Group {
  if (row.hidden) return 'hidden'
  if (row.closed || row.stale) return 'inactive'
  if (DROPPED_STAGES.has(row.stage)) return 'dropped'
  if (miss) return 'mismatch'
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

function failedSources(sources: Record<string, SourceScan> | undefined): number {
  return Object.values(sources ?? {}).filter((s) => s.error).length
}

/** «разберём 1 вакансию / 3 вакансии / 5 вакансий». */
function vacanciesWord(n: number): string {
  const tens = n % 100
  const ones = n % 10
  if (tens >= 11 && tens <= 14) return 'вакансий'
  if (ones === 1) return 'вакансию'
  if (ones >= 2 && ones <= 4) return 'вакансии'
  return 'вакансий'
}

/** "28 сент., 14:05" — when the shown result was found. The result now
 *  outlives the page, so without a time it could be mistaken for a fresh one. */
function whenText(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function SearchPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, isBusy, panel } = useDocumentPanel()

  // The scan is a job on the server, not a request this page holds: the
  // page can be left and come back to, and the result is read off the job.
  const { job: scanJob } = useScanJob()
  // Only the few hundred ms of queueing; the scan itself is `scanJob`.
  const [isQueueing, setQueueing] = useState(false)
  const isScanning = isQueueing || isActive(scanJob)
  const scanNote = scanJob?.status === 'done' ? scanJob.result : null
  const scanError = scanJob?.status === 'failed' ? scanJob.error : null
  const [isTriaging, startTriage] = useTransition()
  const [limit, setLimit] = useState(PAGE_SIZE)
  // Bumped to take the order anew: by «Пересортировать», and after a triage
  // or a clear — actions that reshape the whole list on the user's request.
  const [epoch, setEpoch] = useState(0)

  // Read once on mount; every change below writes it back, so a reload
  // shows the list the way the user left it.
  const [initialFilters] = useState(loadFilters)
  const [visibleBuckets, setVisibleBuckets] = useState<ReadonlySet<BucketKey>>(
    () => new Set(initialFilters.buckets),
  )
  const [sort, setSort] = useState<SortKey>(initialFilters.sort)
  const [scoredOnly, setScoredOnly] = useState(initialFilters.scoredOnly)
  const [showDropped, setShowDropped] = useState(initialFilters.showDropped)
  const [showInactive, setShowInactive] = useState(initialFilters.showInactive)
  const [showHidden, setShowHidden] = useState(initialFilters.showHidden)
  const [showMismatch, setShowMismatch] = useState(initialFilters.showMismatch)
  const [conditions, setConditions] = useState<Conditions>(initialFilters.conditions)
  const [confirmClear, setConfirmClear] = useState(false)

  // Hoisted, not `data?.found ?? []`: a fresh [] on every render makes every
  // useMemo below recompute, which is the opposite of what they are for.
  const rows = data?.found ?? EMPTY_ROWS

  // One pass over the list: the split feeds the counters, the bucket filter
  // and the rendered rows alike. `base` keeps the payload's order, so a
  // toggled-in group lands where its rows were found rather than at the end.
  const { counts, base, scored, misses, untriaged } = useMemo(() => {
    const shownGroups: Record<Group, boolean> = {
      kept: true,
      // Dropped and inactive rows are not shown at all for now, against the
      // «отсев никогда не молчит» rule: the owner's call of 2026-10-02, until
      // the pipeline is redesigned (docs/decisions.md). Still counted, so the
      // rows are one line away from coming back.
      dropped: false,
      // The conditions above the list are a filter like any other: to see
      // what they leave out, the user loosens them (2026-10-02).
      mismatch: false,
      inactive: false,
      hidden: showHidden,
    }
    const counts: Record<Group, number> = {
      kept: 0,
      dropped: 0,
      mismatch: 0,
      inactive: 0,
      hidden: 0,
    }
    const base: FoundVacancy[] = []
    const misses = new Map<string, string>()
    // What «Умный отбор» would pay for: the server's own selection
    // (shortlist, no verdict, not closed, not hidden) narrowed to the rows the
    // conditions keep — a row they fold away is not worth a paid verdict.
    const untriaged: string[] = []
    let scored = 0
    for (const row of rows) {
      const miss = conditionMiss(row, conditions)
      if (miss) misses.set(row.id, miss)
      const group = groupOf(row, miss)
      counts[group]++
      if (group === 'kept' && row.score != null) scored++
      if (group === 'kept' && row.stage === 'shortlist' && !row.triage && row.score == null)
        untriaged.push(row.id)
      if (shownGroups[group]) base.push(row)
    }
    return { counts, base, scored, misses, untriaged }
  }, [rows, conditions, showHidden])

  const bucketCounts = useMemo(() => {
    const counts = new Map<BucketKey, number>()
    for (const row of base) {
      const key = bucketOf(row)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [base])

  const anyJudged = useMemo(() => rows.some((row) => bucketOf(row) != null), [rows])

  const bucketed = useMemo(() => {
    if (!anyJudged) return base
    return base.filter((row) => visibleBuckets.has(bucketOf(row)))
  }, [base, anyJudged, visibleBuckets])

  // Counted before «только оценённые» so the toggle's own number does not
  // collapse to the list it already shows.
  const scoredShown = useMemo(() => bucketed.filter((row) => row.score != null), [bucketed])

  const visible = useMemo(
    () => sortRows(scoredOnly ? scoredShown : bucketed, sort),
    [bucketed, scoredShown, scoredOnly, sort],
  )

  // The order is held until the view changes, the user asks, or a scan
  // brings rows the order has never seen — see frozenOrder.ts. The key has
  // every input the user chooses; a changed score or verdict is not one.
  const viewKey = [
    sort,
    scoredOnly,
    [...visibleBuckets].map(String).sort().join(','),
    showDropped,
    showMismatch,
    showInactive,
    showHidden,
    JSON.stringify(conditions),
    epoch,
  ].join('|')
  // The job turns "done" a moment before its rows arrive, so a new scan
  // retakes the order only once the rows it found are actually here.
  const scanMark = scanJob?.status === 'done' ? scanJob.finished_at : null
  const [frozen, setFrozen] = useState<FrozenOrder | null>(null)
  const liveIds = useMemo(() => visible.map((row) => row.id), [visible])
  const heldIds = useMemo(() => new Set(frozen?.ids), [frozen])
  const retake =
    data !== undefined &&
    (frozen === null ||
      frozen.key !== viewKey ||
      (frozen.scanMark !== scanMark && liveIds.some((id) => !heldIds.has(id))))
  // Adjusting state while rendering, the way React documents for state that
  // follows other state: an effect would paint the unfrozen list first.
  if (retake) setFrozen({ key: viewKey, scanMark, ids: liveIds })

  const ordered = useMemo(
    () => (frozen && !retake ? arrange(frozen.ids, rows, visible) : visible),
    [frozen, retake, rows, visible],
  )
  const changes = useMemo(
    () =>
      pendingChanges(
        ordered.map((row) => row.id),
        liveIds,
      ),
    [ordered, liveIds],
  )

  // Computed from the rendered value rather than inside a state updater: the
  // write to storage is a side effect, and updaters must stay pure.
  function persist(change: Partial<SearchFilters>) {
    saveFilters({
      buckets: [...visibleBuckets],
      sort,
      scoredOnly,
      showDropped,
      showInactive,
      showHidden,
      showMismatch,
      conditions,
      ...change,
    })
  }

  function changeConditions(change: Partial<Conditions>) {
    const next = { ...conditions, ...change }
    setConditions(next)
    setLimit(PAGE_SIZE)
    persist({ conditions: next })
  }

  function toggleBucket(key: BucketKey) {
    const next = new Set(visibleBuckets)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setVisibleBuckets(next)
    setLimit(PAGE_SIZE)
    persist({ buckets: [...next] })
  }

  function chooseSort(next: SortKey) {
    setSort(next)
    setLimit(PAGE_SIZE)
    persist({ sort: next })
  }

  function toggleScoredOnly() {
    setScoredOnly(!scoredOnly)
    setLimit(PAGE_SIZE)
    persist({ scoredOnly: !scoredOnly })
  }

  function toggleGroup(group: Exclude<Group, 'kept'>) {
    const next = {
      showDropped: group === 'dropped' ? !showDropped : showDropped,
      showMismatch: group === 'mismatch' ? !showMismatch : showMismatch,
      showInactive: group === 'inactive' ? !showInactive : showInactive,
      showHidden: group === 'hidden' ? !showHidden : showHidden,
    }
    setShowDropped(next.showDropped)
    setShowMismatch(next.showMismatch)
    setShowInactive(next.showInactive)
    setShowHidden(next.showHidden)
    setLimit(PAGE_SIZE)
    persist(next)
  }

  // Not a transition: an async action keeps every transition pending until
  // it settles, and navigation is one — a minute-long scan awaited here froze
  // the sidebar. Queueing returns at once; the cabinet shell reports the end.
  async function scan() {
    setQueueing(true)
    try {
      const job = await startScan({
        source: 'all',
        salary_min: conditions.salary,
        any_salary: conditions.anySalary,
        remote_only: conditions.remoteOnly,
        accredited_only: conditions.accreditedOnly,
        include_global: conditions.includeGlobal,
      })
      // Seeded with the queued row so polling starts without waiting a round.
      await mutate(SCAN_JOB_KEY, [job])
    } catch (e) {
      toast.error(e)
    } finally {
      setQueueing(false)
    }
  }

  function triage() {
    startTriage(async () => {
      try {
        const result = await runTriage(untriaged)
        await mutate(STATE_KEY)
        setEpoch((current) => current + 1)

        if (result.note) {
          toast.info(result.note)
          return
        }
        toast.info(`Умный отбор: разобрано ${result.judged}.`)
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
    setConfirmClear(false)
    try {
      const result = await clearFound()
      await mutate(STATE_KEY)
      setEpoch((current) => current + 1)
      toast.info(`Убрано ${result.deleted}. Избранное не тронуто.`)
    } catch (e) {
      toast.error(e)
    }
  }

  async function toggleHidden(vacancy: FoundVacancy) {
    try {
      await hideVacancy(vacancy.id, !vacancy.hidden)
    } catch (e) {
      toast.error(e)
    }
  }

  async function toggleSave(vacancy: FoundVacancy) {
    try {
      await saveVacancy(vacancy.id, !vacancy.saved)
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

  const shown = ordered.slice(0, limit)

  return (
    <div className="page">
      <PageHead title="Поиск" />

      <section className="card scan" aria-label="Параметры поиска">
        <div className="scan__head">
          <h2 className="scan__title">Условия</h2>
        </div>

        <div className="scan__grid">
          <SalaryInput
            value={conditions.salary}
            anySalary={conditions.anySalary}
            onChange={(salary) => changeConditions({ salary })}
            onAnySalaryChange={(anySalary) => changeConditions({ anySalary })}
          />

          <div className="scan__side">
            <div className="scan__opts">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={conditions.remoteOnly}
                  onChange={(e) => changeConditions({ remoteOnly: e.target.checked })}
                />
                Удалёнка
              </label>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={conditions.accreditedOnly}
                  onChange={(e) => changeConditions({ accreditedOnly: e.target.checked })}
                />
                ИТ-аккредитованные
              </label>
              {/* A source choice, not a property of a row — the employer's
                  group is not stored — so it cannot filter what was found. */}
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={conditions.includeGlobal}
                  onChange={(e) => changeConditions({ includeGlobal: e.target.checked })}
                />
                Зарубежные
              </label>
            </div>

            <div className="scan__go">
              <button className="btn scan__btn" onClick={scan} disabled={isScanning}>
                {isScanning ? 'Ищу во всех источниках…' : 'Найти вакансии'}
              </button>
            </div>
          </div>
        </div>

        {isScanning ? (
          <div className="scan__progress" role="status">
            <span className="scan__bar" aria-hidden="true" />
            Источники читаются параллельно, модель не вызывается. Можно уйти на другую
            страницу — поиск продолжится, итог появится здесь.
          </div>
        ) : null}

        {scanError && !isScanning ? (
          <div className="scan__result scan__source--err" role="alert">
            Поиск не удался: {scanError}
          </div>
        ) : null}

        {/* Folded: the totals are what a glance needs, the per-source lines
            only matter when a number looks wrong. A failed source is named in
            the summary, so folding never hides an error. */}
        {scanNote && !isScanning ? (
          <details className="scan__result scan__report">
            <summary role="status">
              {scanJob?.finished_at
                ? `Поиск ${whenText(scanJob.finished_at)}: `
                : 'Поиск: '}
              найдено <b className="num">{scanNote.collected}</b>, прошло фильтры{' '}
              <b className="num">{scanNote.kept ?? 0}</b>, новых{' '}
              <b className="num">{scanNote.new}</b>
              {failedSources(scanNote.sources) ? (
                <span className="scan__source--err">
                  {' '}
                  · не прочитано источников: {failedSources(scanNote.sources)}
                </span>
              ) : null}
              <span className="scan__more">подробнее</span>
            </summary>
            {scanNote.duplicates ? (
              <p className="scan__total">
                Уже найдены в другом месте: {scanNote.duplicates}.
              </p>
            ) : null}
            <ul className="scan__sources">
              {Object.entries(scanNote.sources ?? {}).map(([name, s]) => (
                <li key={name} className={s.error ? 'scan__source--err' : undefined}>
                  {sourceLine(name, s)}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      <section aria-label="Найденные вакансии">
        <div className="listbar">
          <div className="listbar__title">
            <h2>Найденные</h2>
            {rows.length ? (
              <span className="muted num">
                {counts.kept} · оценено {scored}
              </span>
            ) : null}
          </div>
          {rows.length ? (
            <button
              className="btn btn--quiet btn--small"
              onClick={() => setConfirmClear(true)}
              title="Убрать из списка всё, кроме избранного"
            >
              Очистить
            </button>
          ) : null}
        </div>

        {/* The step between a free scan and paid evaluations, set apart from
            the list controls: a small ghost button among filters read as one
            more filter, and people evaluated unsorted lists one by one. */}
        {untriaged.length ? (
          <div className="triage-cta">
            <div className="triage-cta__text">
              <p className="triage-cta__title">Умный отбор</p>
              <p className="muted">
                Разберём <span className="num">{untriaged.length}</span>{' '}
                {vacanciesWord(untriaged.length)} по вашему резюме: что стоит посмотреть, а
                что мимо.
              </p>
            </div>
            <button className="btn triage-cta__btn" onClick={triage} disabled={isTriaging}>
              {isTriaging ? 'Отбираю…' : 'Отобрать лучшие'}
            </button>
          </div>
        ) : null}

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
              <span className="muted">Умный отбор ещё не запускался.</span>
            )}
            <div className="listbar__view">
              <div className="seg" role="group" aria-label="Порядок списка">
                <button
                  className="seg__item"
                  aria-pressed={sort === 'fresh'}
                  onClick={() => chooseSort('fresh')}
                  title="В том порядке, в каком вакансии найдены: новые сверху"
                >
                  свежие
                </button>
                <button
                  className="seg__item"
                  aria-pressed={sort === 'score'}
                  onClick={() => chooseSort('score')}
                  title="Оценённые сверху, от лучшего балла; дальше — по вердикту отсева"
                >
                  по баллу
                </button>
              </div>
              {scoredShown.length || scoredOnly ? (
                <label
                  className="toggle"
                  title="Только вакансии с полной оценкой — без очереди на оценку"
                >
                  <input type="checkbox" checked={scoredOnly} onChange={toggleScoredOnly} />
                  только оценённые <span className="num">{scoredShown.length}</span>
                </label>
              ) : null}
            </div>
            <div className="listbar__groups">
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
        ) : ordered.length === 0 ? (
          <div className="card">
            <Empty title="Под фильтр ничего не попало">
              {scoredOnly ? (
                <p>Оценённых среди них нет — снимите «только оценённые».</p>
              ) : (
                <p>
                  Ослабьте условия, включите другие вердикты или покажите скрытые.
                </p>
              )}
            </Empty>
          </div>
        ) : (
          <>
            {changes ? (
              <div className="reorder" role="status">
                <span>
                  Порядок устарел, изменений: <b className="num">{changes}</b>. Сам список
                  не перестраивается, чтобы строки не уезжали из-под курсора.
                </span>
                <button
                  className="btn btn--ghost btn--small"
                  onClick={() => setEpoch((current) => current + 1)}
                >
                  Пересортировать
                </button>
              </div>
            ) : null}
            <div className="vlist">
              {shown.map((vacancy) => (
                <VacancyRow
                  key={vacancy.id}
                  vacancy={vacancy}
                  mismatch={misses.get(vacancy.id)}
                  actions={
                    <>
                      {vacancy.outdated ? (
                        <button
                          className="btn btn--quiet btn--small"
                          onClick={() => openReport(vacancy, true)}
                          disabled={isBusy(`${vacancy.id}:report`)}
                          title="Оценка сделана по прежней версии резюме"
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
                            ? 'Открыть готовый отчёт'
                            : 'Полная оценка вакансии'
                        }
                      >
                        {isBusy(`${vacancy.id}:report`)
                          ? 'Считаю…'
                          : vacancy.score != null
                            ? 'Отчёт'
                            : 'Оценить'}
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
          </>
        )}

        {ordered.length > shown.length ? (
          <div className="more">
            <button
              className="btn btn--ghost"
              onClick={() => setLimit((current) => current + PAGE_SIZE)}
            >
              Показать ещё {Math.min(PAGE_SIZE, ordered.length - shown.length)}
            </button>
            <span className="muted num">
              показано {shown.length} из {ordered.length}
            </span>
          </div>
        ) : null}
      </section>

      <ConfirmDialog
        open={confirmClear}
        title="Очистить список?"
        confirmLabel="Удалить"
        onConfirm={clear}
        onCancel={() => setConfirmClear(false)}
      >
        <p>Вы собираетесь удалить все найденные вакансии, кроме избранных.</p>
        <p className="muted">
          Будет удалено:{' '}
          <span className="num">{rows.filter((row) => !row.saved).length}</span>. Вернуть их
          можно только новым поиском.
        </p>
      </ConfirmDialog>

      {panel}
    </div>
  )
}
