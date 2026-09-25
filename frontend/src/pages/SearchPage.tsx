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

  // Hoisted, not `data?.found ?? []`: a fresh [] on every render makes every
  // useMemo below recompute, which is the opposite of what they are for.
  const rows = data?.found ?? EMPTY_ROWS

  // One pass over the list instead of three filters: the split feeds the
  // counters, the bucket filter and the rendered rows alike.
  const { kept, dropped, scored } = useMemo(() => {
    const kept: FoundVacancy[] = []
    const dropped: FoundVacancy[] = []
    let scored = 0
    for (const row of rows) {
      if (DROPPED_STAGES.has(row.stage)) dropped.push(row)
      else {
        kept.push(row)
        if (row.score != null) scored++
      }
    }
    return { kept, dropped, scored }
  }, [rows])

  const bucketCounts = useMemo(() => {
    const counts = new Map<BucketKey, number>()
    for (const row of showDropped ? rows : kept) {
      const key = row.triage?.bucket ?? null
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [rows, kept, showDropped])

  const anyJudged = useMemo(() => rows.some((row) => row.triage), [rows])

  const visible = useMemo(() => {
    const base = showDropped ? rows : kept
    if (!anyJudged) return base
    return base.filter((row) => visibleBuckets.has(row.triage?.bucket ?? null))
  }, [rows, kept, showDropped, anyJudged, visibleBuckets])

  // Computed from the rendered value rather than inside a state updater: the
  // write to storage is a side effect, and updaters must stay pure.
  function toggleBucket(key: BucketKey) {
    const next = new Set(visibleBuckets)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setVisibleBuckets(next)
    setLimit(PAGE_SIZE)
    saveFilters({ buckets: [...next], showDropped })
  }

  function toggleDropped() {
    setShowDropped(!showDropped)
    setLimit(PAGE_SIZE)
    saveFilters({ buckets: [...visibleBuckets], showDropped: !showDropped })
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
                {kept.length} · оценено {scored}
                {data.spend.free_to_reopen
                  ? ` · ${data.spend.free_to_reopen} откроются бесплатно`
                  : ''}
              </span>
            ) : null}
          </div>
          <div className="row">
            {kept.length ? (
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
            {dropped.length ? (
              <button className="linkish listbar__dropped" onClick={toggleDropped}>
                {showDropped ? 'скрыть' : 'показать'} отсеянные ({dropped.length})
              </button>
            ) : null}
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
              <p>Включите другие вердикты выше или покажите отсеянные.</p>
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
