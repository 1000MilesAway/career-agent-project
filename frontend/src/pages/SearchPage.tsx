/** "Поиск" — collect, screen for free, triage cheaply, evaluate.
 *
 * The order of the stages is the pipeline invariant: nothing paid runs before
 * the free filters have. The screen only ever offers the next stage, so there
 * is no button that skips one.
 */

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSWRConfig } from 'swr'
import {
  SCAN_JOB_KEY,
  STATE_KEY,
  TRIAGE_JOB_KEY,
  clearFound,
  isActive,
  hideVacancy,
  saveVacancy,
  startScan,
  startTriage,
  useAppState,
  useRefreshJob,
  useScanJob,
  useTriageJob,
  useTriageSettled,
} from '../api/hooks'
import type { FoundVacancy, SourceScan } from '../api/types'
import { CitySelect } from '../components/CitySelect'
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
import { Empty, PageHead, Skeleton } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import { StateError } from '../components/Fallback'
import {
  ALL_SOURCES,
  SOURCE_GROUPS,
  SOURCE_OPTION_TITLES,
  type BucketKey,
  type Conditions,
  type SearchFilters,
  type SortKey,
  type SourceGroup,
  DEFAULT_CONDITIONS,
  conditionMiss,
  splitCities,
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
  hh: 'hh.ru',
  company: 'Сайты компаний',
  geekjob: 'geekjob',
  telegram: 'Telegram-каналы',
  telethon: 'Telegram-чаты',
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

/** Is this row from a source the form switched off? A name the form does not
 *  know is never "off": a source added on the server must not disappear
 *  behind a selection stored before it existed. */
function sourceOff(source: string, chosen: readonly string[]): boolean {
  return ALL_SOURCES.includes(source) && !chosen.includes(source)
}

/** One category of sources, with its own «все».
 *
 *  A category with a single source draws no second row that repeats its own
 *  title: the checkbox is the category, and ticking it is «выбрать все» over a
 *  set of one. Several sources get the title, the «все» link and a checkbox
 *  each — the link reads the group, not one row. */
function SourceGroupBox({
  group,
  chosen,
  onToggle,
  onToggleGroup,
}: {
  group: SourceGroup
  chosen: readonly string[]
  onToggle: (name: string) => void
  onToggleGroup: (group: SourceGroup) => void
}) {
  const all = group.sources.every((name) => chosen.includes(name))
  if (group.sources.length === 1 || group.one) {
    return (
      <label className="toggle sources__own">
        <input type="checkbox" checked={all} onChange={() => onToggleGroup(group)} />
        {group.title}
      </label>
    )
  }
  return (
    <div className="sources__group">
      <div className="sources__head">
        <span className="sources__title">{group.title}</span>
        <button
          className="linkish sources__all"
          aria-pressed={all}
          onClick={() => onToggleGroup(group)}
        >
          {all ? 'скрыть все' : 'выбрать все'}
        </button>
      </div>
      {group.sources.map((name) => (
        <label className="toggle sources__item" key={name}>
          <input
            type="checkbox"
            checked={chosen.includes(name)}
            onChange={() => onToggle(name)}
          />
          {SOURCE_OPTION_TITLES[name] ?? name}
        </label>
      ))}
    </div>
  )
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
  // The listings the scan answered from a stale pool are re-read behind it,
  // a minute or two later. Unannounced, the list changing then read as noise.
  const { job: refreshJob } = useRefreshJob()
  const followsScan =
    scanNote !== null &&
    refreshJob !== null &&
    Date.parse(refreshJob.created_at) >= Date.parse(scanJob!.created_at)
  const isRefreshing = followsScan && isActive(refreshJob)
  // Rows found since the scan was started — by it or by its refresh. Counted
  // off the list itself: the server's «new» also counts rows the list folds,
  // and «новых 21» over 19 rows was the complaint.
  const newSince = scanNote ? Date.parse(scanJob!.created_at) : null
  const [onlyNew, setOnlyNew] = useState(false)
  // Triage is a job as well: the button only queues it, and the cabinet
  // shell reports the verdicts wherever the user is by then.
  const { job: triageJob } = useTriageJob()
  const [isQueueingTriage, setQueueingTriage] = useState(false)
  const isTriaging = isQueueingTriage || isActive(triageJob)
  const triageSettled = useTriageSettled()
  const [limit, setLimit] = useState(PAGE_SIZE)
  // Bumped to take the order anew: by «Пересортировать», and after a clear
  // (a finished triage does the same through `triageSettled`) — actions that reshape the whole list on the user's request.
  const [epoch, setEpoch] = useState(0)

  // Read once on mount; every change below writes it back, so a reload
  // shows the list the way the user left it.
  const [initialFilters] = useState(loadFilters)
  const [visibleBuckets, setVisibleBuckets] = useState<ReadonlySet<BucketKey>>(
    () => new Set(initialFilters.buckets),
  )
  const [sort, setSort] = useState<SortKey>(initialFilters.sort)
  const [hideScored, setHideScored] = useState(initialFilters.hideScored)
  const [showDropped, setShowDropped] = useState(initialFilters.showDropped)
  const [showInactive, setShowInactive] = useState(initialFilters.showInactive)
  const [showHidden, setShowHidden] = useState(initialFilters.showHidden)
  const [showMismatch, setShowMismatch] = useState(initialFilters.showMismatch)
  // Rows of a source the form switched off. Folded — never dropped: the
  // choice is the user's, and one click has to bring them back.
  const [showOffSources, setShowOffSources] = useState(false)
  const [chosenConditions, setConditions] = useState<Conditions>(initialFilters.conditions)
  // A city never typed here is the one the CV gives — derived, not copied
  // into state, so editing the CV moves it until the user picks their own.
  const resumeCity = data?.profile?.preferences?.city ?? ''
  const conditions = useMemo(
    () => ({ ...chosenConditions, city: chosenConditions.city ?? resumeCity }),
    [chosenConditions, resumeCity],
  )
  // Said on the button while a scan runs: «во всех источниках» over a form
  // with one source ticked read as a promise the scan was not keeping.
  const allSourcesChosen = conditions.sources.length === ALL_SOURCES.length
  // Cheap and honest: both objects are built by loadFilters, key order and
  // all, so the strings match while nothing has been touched.
  const canReset = JSON.stringify(chosenConditions) !== JSON.stringify(DEFAULT_CONDITIONS)
  const [confirmClear, setConfirmClear] = useState(false)

  // Hoisted, not `data?.found ?? []`: a fresh [] on every render makes every
  // useMemo below recompute, which is the opposite of what they are for.
  // Without the vacancies the user added by link: those live in
  // «Избранное», and neither triage nor «Очистить» touches them.
  const found = data?.found
  const rows = useMemo(
    () => (found ? found.filter((row) => !row.added_at) : EMPTY_ROWS),
    [found],
  )

  // One pass over the list: the split feeds the counters, the bucket filter
  // and the rendered rows alike. `base` keeps the payload's order, so a
  // toggled-in group lands where its rows were found rather than at the end.
  const { counts, base, scored, misses, untriaged, fresh, freshFolded, offSources } =
    useMemo(() => {
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
      // New rows the list shows, and new rows it folds (closed, old, not under
      // the conditions): the summary names both, so the numbers add up.
      const fresh = new Set<string>()
      let freshFolded = 0
      // Counted whether or not they are shown, so the toggle that brings them
      // back keeps its number while they are on screen.
      let offSources = 0
      for (const row of rows) {
        if (sourceOff(row.source, conditions.sources)) {
          offSources++
          if (!showOffSources) continue
        }
        const miss = conditionMiss(row, conditions)
        if (miss) misses.set(row.id, miss)
        const group = groupOf(row, miss)
        counts[group]++
        if (newSince !== null && row.found_at && Date.parse(row.found_at) >= newSince) {
          if (group === 'kept') fresh.add(row.id)
          else if (group !== 'hidden') freshFolded++
        }
        if (group === 'kept' && row.score != null) scored++
        if (
          group === 'kept' &&
          row.stage === 'shortlist' &&
          !row.triage &&
          row.score == null
        )
          untriaged.push(row.id)
        if (shownGroups[group]) base.push(row)
      }
      return { counts, base, scored, misses, untriaged, fresh, freshFolded, offSources }
    }, [rows, conditions, showHidden, showOffSources, newSince])
  // A filter with nothing left to show lets itself go rather than leave an
  // empty list behind a summary that says «новых 0».
  const showOnlyNew = onlyNew && fresh.size > 0

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

  // Counted before «скрыть оценённые» so the toggle's own number — what the
  // filter would take away — does not collapse to an empty list.
  const scoredShown = useMemo(() => bucketed.filter((row) => row.score != null), [bucketed])

  const visible = useMemo(() => {
    const shown = hideScored ? bucketed.filter((row) => row.score == null) : bucketed
    return sortRows(showOnlyNew ? shown.filter((row) => fresh.has(row.id)) : shown, sort)
  }, [bucketed, hideScored, sort, showOnlyNew, fresh])

  // The order is held until the view changes, the user asks, or a scan
  // brings rows the order has never seen — see frozenOrder.ts. The key has
  // every input the user chooses; a changed score or verdict is not one.
  const viewKey = [
    sort,
    hideScored,
    [...visibleBuckets].map(String).sort().join(','),
    JSON.stringify(conditions.sources),
    showOffSources,
    showDropped,
    showMismatch,
    showInactive,
    showHidden,
    JSON.stringify(conditions),
    epoch,
    showOnlyNew,
    // A finished triage reshapes the list like a clear does — once its
    // verdicts are in the rows (see useTriageSettled).
    triageSettled,
  ].join('|')
  // The job turns "done" a moment before its rows arrive, so a new scan
  // retakes the order only once the rows it found are actually here. The
  // refresh behind it counts as part of it: its rows used to land after the
  // last held row, past «показать ещё», where nobody found them.
  const scanMark =
    scanJob?.status === 'done'
      ? `${scanJob.finished_at}|${followsScan && refreshJob!.status === 'done' ? refreshJob!.finished_at : ''}`
      : null
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
      hideScored,
      showDropped,
      showInactive,
      showHidden,
      showMismatch,
      conditions: chosenConditions,
      ...change,
    })
  }

  function changeConditions(change: Partial<Conditions>) {
    const next = { ...chosenConditions, ...change }
    setConditions(next)
    setLimit(PAGE_SIZE)
    persist({ conditions: next })
  }

  /** Back to how the panel opens — the conditions, not the row filters below
   *  the list: those are their own switches with their own buttons. */
  function resetConditions() {
    changeConditions(DEFAULT_CONDITIONS)
  }

  function toggleSource(name: string) {
    const next = conditions.sources.includes(name)
      ? conditions.sources.filter((source) => source !== name)
      : [...conditions.sources, name]
    changeConditions({ sources: next })
  }

  /** The category's «все»: with every source of it on, it switches the lot
   *  off; otherwise it takes them all. One button, because a category of two
   *  or three does not need a third state nobody asked for. */
  function toggleSourceGroup(group: SourceGroup) {
    const all = group.sources.every((name) => conditions.sources.includes(name))
    const rest = conditions.sources.filter((name) => !group.sources.includes(name))
    changeConditions({ sources: all ? rest : [...rest, ...group.sources] })
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

  function toggleHideScored() {
    setHideScored(!hideScored)
    setLimit(PAGE_SIZE)
    persist({ hideScored: !hideScored })
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
    setOnlyNew(false)
    try {
      const job = await startScan({
        source: 'all',
        sources: conditions.sources,
        salary_min: conditions.salary,
        any_salary: conditions.anySalary,
        remote_only: conditions.remoteOnly,
        accredited_only: conditions.accreditedOnly,
        cities: splitCities(conditions.city),
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

  async function triage() {
    setQueueingTriage(true)
    try {
      const job = await startTriage(untriaged)
      // Seeded with the queued row so polling starts without waiting a round.
      await mutate(TRIAGE_JOB_KEY, [job])
    } catch (e) {
      toast.error(e)
    } finally {
      setQueueingTriage(false)
    }
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

  if (error) return <StateError error={error} />
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
            <Link className="btn" to="/resume">
              Загрузить резюме
            </Link>
          </Empty>
        </div>
      </div>
    )
  }

  const shown = ordered.slice(0, limit)

  return (
    <div className="page search">
      <PageHead
        title="Поиск"
        actions={
          <button
            className="btn scan__btn"
            onClick={scan}
            disabled={isScanning || !conditions.sources.length}
            title={conditions.sources.length ? undefined : 'Выберите хотя бы один источник'}
          >
            {isScanning
              ? allSourcesChosen
                ? 'Ищу во всех источниках…'
                : 'Ищу в выбранных источниках…'
              : 'Найти вакансии'}
          </button>
        }
      />

      {/* Filters as a facet panel on the left of the results — the shape every
          job board has, and the one that reads without a legend: conditions in
          one column, what they found beside them. Each facet starts with its
          own caption line, so the groups line up down the panel. */}
      <div className="search__layout">
        <div className="search__sidebar">
          <aside className="card facets" aria-label="Параметры поиска">
            <div className="facets__head">
              <h2 className="facets__title">Условия</h2>
              {/* Only when something differs from how the panel opens: a reset
                that is always there says «you changed something» when you did
                not. */}
              {canReset ? (
                <button className="linkish facets__reset" onClick={resetConditions}>
                  сбросить
                </button>
              ) : null}
            </div>

            {/* No facet caption here: the field's own label («Зарплата от») sits
              on that line and names the group. */}
            <div className="facet">
              <SalaryInput
                value={conditions.salary}
                anySalary={conditions.anySalary}
                onChange={(salary) => changeConditions({ salary })}
                onAnySalaryChange={(anySalary) => changeConditions({ anySalary })}
              />
            </div>

            <div className="facet">
              <label className="facet__label" htmlFor="scan-city">
                Город
              </label>
              <CitySelect
                id="scan-city"
                value={conditions.city ?? ''}
                emptyLabel="Любой город"
                onChange={(city) => changeConditions({ city })}
              />
              <label className="toggle facet__toggle">
                <input
                  type="checkbox"
                  checked={conditions.remoteOnly}
                  onChange={(e) => changeConditions({ remoteOnly: e.target.checked })}
                />
                Удалёнка
              </label>
              <label className="toggle facet__toggle">
                <input
                  type="checkbox"
                  checked={conditions.accreditedOnly}
                  onChange={(e) => changeConditions({ accreditedOnly: e.target.checked })}
                />
                ИТ-аккредитованные
              </label>
              {/* A source choice, not a property of a row — the employer's group
                is not stored — so it cannot filter what was found. */}
              <label className="toggle facet__toggle">
                <input
                  type="checkbox"
                  checked={conditions.includeGlobal}
                  onChange={(e) => changeConditions({ includeGlobal: e.target.checked })}
                />
                Зарубежные
              </label>
            </div>

            {/* Source controls use the same toggle as the other conditions. */}
            <div
              className="facet facet--sources"
              role="group"
              aria-labelledby="scan-sources"
            >
              <span className="facet__label" id="scan-sources">
                Источники
              </span>
              <div className="sources">
                {SOURCE_GROUPS.map((group) => (
                  <SourceGroupBox
                    key={group.key}
                    group={group}
                    chosen={conditions.sources}
                    onToggle={toggleSource}
                    onToggleGroup={toggleSourceGroup}
                  />
                ))}
              </div>
            </div>
          </aside>

          <div className="scan__go">
            {!conditions.sources.length && !isScanning ? (
              <span className="muted">Искать негде: выберите хотя бы один источник.</span>
            ) : null}
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
              <div className="scan__result">
                <div className="scan__fresh" role="status">
                  {scanJob?.finished_at
                    ? `Поиск ${whenText(scanJob.finished_at)}: `
                    : 'Поиск: '}
                  {fresh.size ? (
                    <button
                      className="linkish"
                      aria-pressed={showOnlyNew}
                      onClick={() => setOnlyNew(!showOnlyNew)}
                      title={
                        showOnlyNew
                          ? 'Показать весь список'
                          : 'Показать в списке только новые'
                      }
                    >
                      {showOnlyNew ? 'показаны только новые' : 'новых в списке'}{' '}
                      <b className="num">{fresh.size}</b>
                    </button>
                  ) : (
                    <>новых вакансий нет</>
                  )}
                  {freshFolded ? (
                    <span className="muted">
                      {' '}
                      · ещё <span className="num">{freshFolded}</span> закрыты или не под
                      условия
                    </span>
                  ) : null}
                </div>
                {isRefreshing ? (
                  <div className="scan__progress" role="status">
                    <span className="scan__bar" aria-hidden="true" />
                    Дочитываю источники, где данные устарели, — новые вакансии добавятся в
                    список сами.
                  </div>
                ) : null}
                <details className="scan__report">
                  <summary>
                    найдено <b className="num">{scanNote.collected}</b>, прошло фильтры{' '}
                    <b className="num">{scanNote.kept ?? 0}</b>
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
              </div>
            ) : null}
          </div>
        </div>
        <section className="results" aria-label="Найденные вакансии">
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
                  {vacanciesWord(untriaged.length)} по вашему резюме: что стоит посмотреть,
                  а что мимо.
                </p>
              </div>
              <button
                className="btn triage-cta__btn"
                onClick={triage}
                disabled={isTriaging}
              >
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
                {scoredShown.length || hideScored ? (
                  <label
                    className="toggle"
                    title="Убрать вакансии с полной оценкой — оставить очередь на оценку"
                  >
                    <input
                      type="checkbox"
                      checked={hideScored}
                      onChange={toggleHideScored}
                    />
                    скрыть оценённые <span className="num">{scoredShown.length}</span>
                  </label>
                ) : null}
              </div>
              <div className="listbar__groups">
                {offSources ? (
                  <button
                    className="linkish listbar__dropped"
                    onClick={() => setShowOffSources(!showOffSources)}
                  >
                    {showOffSources ? 'убрать' : 'показать'} другие источники ({offSources})
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
                <p>Задайте зарплату и условия и запустите поиск — это бесплатно.</p>
              </Empty>
            </div>
          ) : ordered.length === 0 ? (
            <div className="card">
              <Empty title="Под фильтр ничего не попало">
                {hideScored ? (
                  <p>Оценённые скрыты — снимите «скрыть оценённые».</p>
                ) : (
                  <p>Ослабьте условия, включите другие вердикты или покажите скрытые.</p>
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
                    isNew={fresh.has(vacancy.id)}
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
      </div>

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
