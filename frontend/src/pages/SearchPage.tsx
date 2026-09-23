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
import type { FoundVacancy, ScanResult, TriageBucket } from '../api/types'
import { SalaryInput } from '../components/SalaryInput'
import { BUCKET_TITLES, DROPPED_STAGES, VacancyRow } from '../components/VacancyRow'
import { useToast } from '../components/toast'
import { Card, ErrorNote, Skeleton, rub } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'

/** Buckets on screen. "skip" is off by default — the point is a short list —
 *  but one click brings it back. `null` is "no verdict yet". */
type BucketKey = TriageBucket | null

/** A stable empty list, so a state payload that has not arrived yet does not
 *  hand every memo below a new array identity on each render. */
const EMPTY_ROWS: FoundVacancy[] = []

/** One line per scan. Duplicates are named apart from the filter's drops:
 *  they are not rejected, they are already on the list from another source. */
function scanSummary(source: string, result: ScanResult): string {
  const dups = result.duplicates
    ? `, уже найдены в другом источнике ${result.duplicates}`
    : ''
  return (
    `${source}: собрано ${result.collected} → отсеяно бесплатно ${result.dropped}${dups}` +
    ` → новых ${result.new}.`
  )
}

export function SearchPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, isBusy, panel } = useDocumentPanel()

  const [salary, setSalary] = useState<number | null>(null)
  const [anySalary, setAnySalary] = useState(true)
  const [remoteOnly, setRemoteOnly] = useState(true)
  const [pages, setPages] = useState(5)
  const [tgPages, setTgPages] = useState(1)
  const [tgAdjacent, setTgAdjacent] = useState(false)

  const [scanNote, setScanNote] = useState<string | null>(null)
  const [tgNote, setTgNote] = useState<string | null>(null)
  const [isScanning, startScan] = useTransition()
  const [isTgScanning, startTgScan] = useTransition()
  const [isGjScanning, startGjScan] = useTransition()
  const [gjPages, setGjPages] = useState(5)
  const [isCoScanning, startCoScan] = useTransition()
  const [coGlobal, setCoGlobal] = useState(false)
  const [isTriaging, startTriage] = useTransition()

  const [visibleBuckets, setVisibleBuckets] = useState<ReadonlySet<BucketKey>>(
    () => new Set<BucketKey>(['worth', 'maybe', null]),
  )
  const [showDropped, setShowDropped] = useState(false)

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

  function toggleBucket(key: BucketKey) {
    setVisibleBuckets((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  function scanHabr() {
    setScanNote(null)
    startScan(async () => {
      try {
        const result = await scanVacancies({
          source: 'habr',
          pages,
          salary_min: salary,
          any_salary: anySalary,
          remote_only: remoteOnly,
        })
        setScanNote(scanSummary('Хабр', result))
        await mutate(STATE_KEY)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function scanTelegram() {
    setTgNote(null)
    startTgScan(async () => {
      try {
        const result = await scanVacancies({
          source: 'telegram',
          pages: tgPages,
          groups: tgAdjacent ? ['core', 'adjacent'] : ['core'],
        })
        // A dead channel still answers 200 with last year's posts, so a
        // skipped one is said out loud rather than passing for healthy.
        const problems = result.problems?.length
          ? ` Пропущены: ${result.problems.map((p) => p.why).join(' · ')}`
          : ''
        setTgNote(
          `Каналов ${result.channels?.length ?? 0}. ${scanSummary('Telegram', result)}${problems}`,
        )
        await mutate(STATE_KEY)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function scanGeekjob() {
    setScanNote(null)
    startGjScan(async () => {
      try {
        const result = await scanVacancies({ source: 'geekjob', pages: gjPages })
        setScanNote(scanSummary('geekjob', result))
        await mutate(STATE_KEY)
      } catch (e) {
        toast.error(e)
      }
    })
  }

  function scanCompanies() {
    setScanNote(null)
    startCoScan(async () => {
      try {
        const result = await scanVacancies({
          source: 'company',
          pages: 1,
          groups: coGlobal ? ['ru', 'global'] : ['ru'],
        })
        // A board that failed is named, not folded into a smaller total.
        const problems = result.problems?.length
          ? ` Не прочитаны: ${result.problems.map((p) => `${p.board}: ${p.why}`).join(' · ')}`
          : ''
        setScanNote(`${scanSummary('Сайты компаний', result)}${problems}`)
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

  if (error) return <ErrorNote error={error} />
  if (isLoading || !data) return <Skeleton rows={6} />

  if (!data.has_resume) {
    return (
      <Card title="Подбор">
        <p style={{ marginTop: 0 }}>
          Сначала загрузите резюме — по нему строятся запросы и фильтры.
        </p>
        <a className="btn" href="#/resume">
          К резюме
        </a>
      </Card>
    )
  }

  return (
    <>
      <Card title="Подбор">
        <p className="muted" style={{ marginTop: 0 }}>
          Сканер подбирает запросы и стоп-слова по вашему резюме, собирает вакансии и
          отсеивает лишние <b>бесплатно</b>. Модель считает только то, что прошло отбор.
        </p>

        <SalaryInput value={salary} anySalary={anySalary} onChange={setSalary} />

        <div className="row" style={{ marginTop: 12 }}>
          <label
            className="check"
            title="Хабр отдаёт только вакансии с указанной вилкой, если задан порог. С галочкой берём и те, где зарплата не указана — их оценят бесплатные фильтры по заголовку."
          >
            <input
              type="checkbox"
              checked={anySalary}
              onChange={(e) => setAnySalary(e.target.checked)}
            />
            учитывать вакансии без указанной зарплаты
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={remoteOnly}
              onChange={(e) => setRemoteOnly(e.target.checked)}
            />
            только удалёнка
          </label>
          <label className="check">
            страниц
            <input
              type="number"
              className="select"
              style={{ width: 62 }}
              min={1}
              max={20}
              value={pages}
              onChange={(e) => setPages(Number(e.target.value) || 1)}
            />
          </label>
          <button className="btn" onClick={scanHabr} disabled={isScanning}>
            {isScanning ? 'Собираю…' : 'Подобрать'}
          </button>
        </div>

        {scanNote ? (
          <p className="muted" role="status">
            {scanNote}
          </p>
        ) : null}

        <div
          className="row"
          style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 12 }}
        >
          <span className="muted">Второй источник:</span>
          <label className="check">
            страниц на канал
            <input
              type="number"
              className="select"
              style={{ width: 62 }}
              min={1}
              max={5}
              value={tgPages}
              onChange={(e) => setTgPages(Number(e.target.value) || 1)}
            />
          </label>
          <label
            className="check"
            title="core — каналы, проверенные на этом профиле; adjacent — другие стеки"
          >
            <input
              type="checkbox"
              checked={tgAdjacent}
              onChange={(e) => setTgAdjacent(e.target.checked)}
            />
            и смежные каналы
          </label>
          <button
            className="btn btn--ghost"
            onClick={scanTelegram}
            disabled={isTgScanning}
            title="Публичное превью t.me/s/ — сбор и бесплатные фильтры, модель не вызывается"
          >
            {isTgScanning ? 'Читаю каналы…' : 'Telegram-каналы'}
          </button>
        </div>

        {tgNote ? (
          <p className="muted" role="status">
            {tgNote}
          </p>
        ) : null}

        <div
          className="row"
          style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 12 }}
        >
          <span className="muted">geekjob.ru:</span>
          <label
            className="check"
            title="На странице 20 вакансий, примерно два дня публикаций"
          >
            страниц
            <input
              type="number"
              className="select"
              style={{ width: 62 }}
              min={1}
              max={15}
              value={gjPages}
              onChange={(e) => setGjPages(Number(e.target.value) || 1)}
            />
          </label>
          <button
            className="btn btn--ghost"
            onClick={scanGeekjob}
            disabled={isGjScanning}
            title="Свежие вакансии geekjob.ru — сбор и бесплатные фильтры по вашим ключевым словам, модель не вызывается"
          >
            {isGjScanning ? 'Читаю geekjob…' : 'geekjob'}
          </button>
        </div>

        <div
          className="row"
          style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 12 }}
        >
          <span className="muted">Сайты компаний:</span>
          <label
            className="check"
            title="Nebius, JetBrains, Wrike, Xsolla и другие компании с русскоязычными командами — вакансии обычно за рубежом и на английском"
          >
            <input
              type="checkbox"
              checked={coGlobal}
              onChange={(e) => setCoGlobal(e.target.checked)}
            />
            и зарубежные компании
          </label>
          <button
            className="btn btn--ghost"
            onClick={scanCompanies}
            disabled={isCoScanning}
            title="Сбер, Яндекс, VK и компании на Huntflow — прямо с их карьерных сайтов. Сбор и бесплатные фильтры, модель не вызывается"
          >
            {isCoScanning ? 'Читаю сайты…' : 'Сайты компаний'}
          </button>
        </div>
      </Card>

      <Card
        title="Найденные вакансии"
        actions={
          <div className="row">
            {kept.length ? (
              <button
                className="btn btn--ghost btn--small"
                onClick={triage}
                disabled={isTriaging}
                title="Один дешёвый прогон: разложить список на «стоит посмотреть / может быть / мимо»"
              >
                {isTriaging ? 'Отсеиваю…' : 'Отсеять моделью'}
              </button>
            ) : null}
            {rows.length ? (
              <button
                className="btn btn--ghost btn--small"
                onClick={clear}
                title="Убрать из списка всё, кроме избранного"
              >
                Очистить
              </button>
            ) : null}
          </div>
        }
      >
        {rows.length ? (
          <p className="muted" style={{ marginTop: 0 }}>
            Найдено <b>{kept.length}</b>, оценено {scored}.{' '}
            {dropped.length ? (
              <button className="linkish" onClick={() => setShowDropped((on) => !on)}>
                {showDropped ? 'скрыть' : 'показать'} отсеянные ({dropped.length})
              </button>
            ) : null}
          </p>
        ) : (
          <p className="muted" style={{ marginTop: 0 }}>
            Пока ничего не найдено — запустите подбор.
          </p>
        )}

        <SpendLine spend={data.spend} />

        {anyJudged ? (
          <div className="row" style={{ margin: '8px 0' }}>
            {(['worth', 'maybe', 'skip'] as const).map((bucket) => (
              <button
                key={bucket}
                className={
                  visibleBuckets.has(bucket)
                    ? 'btn btn--ghost btn--small btn--on'
                    : 'btn btn--ghost btn--small'
                }
                aria-pressed={visibleBuckets.has(bucket)}
                onClick={() => toggleBucket(bucket)}
              >
                {BUCKET_TITLES[bucket]} {bucketCounts.get(bucket) ?? 0}
              </button>
            ))}
            {bucketCounts.get(null) ? (
              <button
                className={
                  visibleBuckets.has(null)
                    ? 'btn btn--ghost btn--small btn--on'
                    : 'btn btn--ghost btn--small'
                }
                aria-pressed={visibleBuckets.has(null)}
                onClick={() => toggleBucket(null)}
              >
                без вердикта {bucketCounts.get(null)}
              </button>
            ) : null}
          </div>
        ) : null}

        {visible.map((vacancy) => (
          <VacancyRow
            key={vacancy.id}
            vacancy={vacancy}
            actions={
              <>
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
                </button>
                <button
                  className={
                    vacancy.saved
                      ? 'btn btn--ghost btn--small btn--done'
                      : 'btn btn--ghost btn--small'
                  }
                  onClick={() => toggleSave(vacancy)}
                  title={
                    vacancy.saved
                      ? 'Уже в избранном — нажмите, чтобы убрать'
                      : 'Отложить: материалы и статусы готовятся там'
                  }
                >
                  {vacancy.saved ? 'В избранном ✓' : 'В избранное'}
                </button>
              </>
            }
          />
        ))}
      </Card>

      {panel}
    </>
  )
}

/** The running total: "what have I spent so far", which is the question a
 *  paying user actually asks. */
function SpendLine({
  spend,
}: {
  spend: {
    found: number
    scored: number
    with_artifacts: number
    rub: number
    free_to_reopen: number
    limit_rub: number | null
  }
}) {
  if (!spend.found) return null
  return (
    <p className="cost">
      Найдено {spend.found} · оценено {spend.scored} · с материалами {spend.with_artifacts}{' '}
      · потрачено <b>{rub(spend.rub)}</b>
      {spend.free_to_reopen
        ? ` · ${spend.free_to_reopen} можно открыть повторно бесплатно`
        : ''}
      {spend.limit_rub != null ? ` · остаток бюджета ${rub(spend.limit_rub)}` : ''}
    </p>
  )
}
