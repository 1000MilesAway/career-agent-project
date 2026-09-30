/** "Избранное" — what the user actually works on.
 *
 * Materials and the tracker live here and nowhere else: the search page
 * decides what is worth keeping, this one does the work on it. Clearing the
 * search list never touches these rows — they carry reports and documents
 * that were paid for.
 *
 * The statuses run across the top as a funnel with counts, the shape job
 * trackers settled on: "where am I stuck" is answered by the numbers before
 * any row is read.
 *
 * A row can be dragged onto a stage of the funnel to move it there. The
 * select in the row stays: native drag and drop does not work with touch on
 * most phones, and it is the keyboard's way in too.
 */

import { useMemo, useState, useTransition, type DragEvent } from 'react'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  tailoredExportBase,
  recheckVacancies,
  saveVacancy,
  moveVacancy,
  useAppState,
} from '../api/hooks'
import type { ApplyStep, FoundVacancy } from '../api/types'
import { DownloadButtons } from '../components/DownloadButtons'
import { VacancyRow } from '../components/VacancyRow'
import { CloseIcon } from '../components/icons'
import { useToast } from '../components/toast'
import { Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import './saved.css'

/** `null` is "all". */
type StatusFilter = string | null

/** A private type, so text or a link dragged in from elsewhere is not taken
 *  for a vacancy. */
const DRAG_TYPE = 'application/x-career-vacancy'

export function SavedPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, openArtifact, isBusy, panel } = useDocumentPanel()
  const [filter, setFilter] = useState<StatusFilter>(null)
  const [dragged, setDragged] = useState<FoundVacancy | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)

  const found = data?.found
  const saved = useMemo(() => found?.filter((row) => row.saved) ?? [], [found])
  const [isRechecking, startRecheck] = useTransition()

  const counts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const row of saved) {
      const key = row.status ?? 'none'
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [saved])

  // A filter on a status that has just emptied would show a blank list with
  // no way to tell why; fall back to "all" instead.
  const active = filter && counts.get(filter) ? filter : null
  const shown = active ? saved.filter((row) => (row.status ?? 'none') === active) : saved

  function recheck() {
    startRecheck(async () => {
      try {
        const result = await recheckVacancies()
        await mutate(STATE_KEY)
        // "Unknown" is said separately: a page that did not answer is not an
        // open vacancy, and folding it into "open" would overstate the check.
        const unknown = result.unknown ? `, не удалось проверить ${result.unknown}` : ''
        toast.info(
          result.checked
            ? `Проверено ${result.checked}: открыты ${result.open}, закрыты ${result.closed}${unknown}.`
            : 'Проверять нечего: всё проверено за последние 12 часов или источник не умеет отвечать.',
        )
      } catch (e) {
        toast.error(e)
      }
    })
  }

  async function unsave(id: string) {
    try {
      await saveVacancy(id, false)
    } catch (e) {
      toast.error(e)
    }
  }

  async function move(id: string, status: string) {
    try {
      await moveVacancy(id, status)
    } catch (e) {
      toast.error(e)
    }
  }

  function startDrag(e: DragEvent, vacancy: FoundVacancy) {
    e.dataTransfer.setData(DRAG_TYPE, vacancy.id)
    e.dataTransfer.effectAllowed = 'move'
    // Deferred: Chrome cancels a drag whose source changes inside dragstart,
    // and React re-renders a discrete event before it returns.
    setTimeout(() => setDragged(vacancy))
  }

  function endDrag() {
    setDragged(null)
    setDropTarget(null)
  }

  function dragOver(e: DragEvent, key: string) {
    if (!e.dataTransfer.types.includes(DRAG_TYPE)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dropTarget !== key) setDropTarget(key)
  }

  function drop(e: DragEvent, key: string) {
    const id = e.dataTransfer.getData(DRAG_TYPE)
    if (!id) return
    e.preventDefault()
    endDrag()
    const row = saved.find((r) => r.id === id)
    if (!row || (row.status ?? 'none') === key) return
    void move(id, key)
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
        <Skeleton rows={4} />
      </div>
    )

  return (
    <div className="page">
      <PageHead
        title="Избранное"
        lede="Вакансии, которые вы отложили. Здесь готовятся материалы к отклику и отмечается, на каком вы этапе. Очистка поиска их не трогает."
        actions={
          saved.length ? (
            <button
              className="btn btn--ghost btn--small"
              onClick={recheck}
              disabled={isRechecking}
              title="Спросить у Хабра, geekjob и Telegram, не закрыты ли вакансии. Бесплатно, модель не вызывается. Вакансии с сайтов работодателей так проверить нельзя."
            >
              {isRechecking ? 'Проверяю…' : 'Проверить, открыты ли'}
            </button>
          ) : null
        }
      />

      {saved.length ? (
        <>
          <div className="funnel" role="group" aria-label="Этап отклика">
            <button
              className="funnel__step"
              aria-pressed={active === null}
              onClick={() => setFilter(null)}
            >
              <span className="funnel__count">{saved.length}</span>
              <span className="funnel__label">В работе</span>
            </button>
            {Object.entries(data.statuses).map(([key, status]) => {
              const count = counts.get(key) ?? 0
              const classes = ['funnel__step']
              if (status.terminal) classes.push('funnel__step--end')
              if (dragged) classes.push('funnel__step--target')
              if (dropTarget === key) classes.push('funnel__step--over')
              return (
                <button
                  key={key}
                  className={classes.join(' ')}
                  aria-pressed={active === key}
                  // A disabled button receives no drag events, so an empty
                  // stage opens up for the length of a drag.
                  disabled={!count && !dragged}
                  onClick={() => setFilter(active === key ? null : key)}
                  onDragOver={(e) => dragOver(e, key)}
                  onDragLeave={() => setDropTarget((t) => (t === key ? null : t))}
                  onDrop={(e) => drop(e, key)}
                >
                  <span className="funnel__count">{count}</span>
                  <span className="funnel__label">{status.title}</span>
                </button>
              )
            })}
          </div>

          <p className="muted funnel__hint">
            {dragged
              ? `Отпустите на этапе, куда перенести «${dragged.title}».`
              : 'Вакансию можно перетащить на этап выше.'}
          </p>

          <div className="vlist">
            {shown.map((vacancy) => (
              <div
                key={vacancy.id}
                className={dragged?.id === vacancy.id ? 'drag drag--active' : 'drag'}
                draggable
                onDragStart={(e) => startDrag(e, vacancy)}
                onDragEnd={endDrag}
              >
                <VacancyRow
                  vacancy={vacancy}
                  actions={
                    <>
                      {vacancy.score != null ? (
                        <>
                          <label className="sr-only" htmlFor={`status-${vacancy.id}`}>
                            Статус отклика
                          </label>
                          <select
                            id={`status-${vacancy.id}`}
                            className={`select status status--${vacancy.status ?? 'none'}`}
                            value={vacancy.status ?? 'none'}
                            onChange={(e) => move(vacancy.id, e.target.value)}
                          >
                            {Object.entries(data.statuses).map(([key, value]) => (
                              <option key={key} value={key}>
                                {value.title}
                              </option>
                            ))}
                          </select>
                        </>
                      ) : null}
                      <button
                        className="btn btn--quiet btn--small btn--icon"
                        onClick={() => unsave(vacancy.id)}
                        title="Убрать из избранного"
                        aria-label={`Убрать «${vacancy.title}» из избранного`}
                      >
                        <CloseIcon />
                      </button>
                    </>
                  }
                  footer={
                    <>
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

                      {/* One button per document. A
                        package made people pay for documents they did not ask
                        for — see docs/decisions.md, 2026-09-18. */}
                      {vacancy.score != null ? (
                        <>
                          <span className="footer__sep" aria-hidden="true" />
                          {data.apply_steps.map((step) => (
                            <ArtifactButton
                              key={step.step}
                              step={step}
                              vacancy={vacancy}
                              busy={isBusy(`${vacancy.id}:${step.step}`)}
                              onClick={() => openArtifact(vacancy, step.step)}
                            />
                          ))}
                        </>
                      ) : null}

                      {/* The tailored CV is the one document that leaves the
                        cabinet — it is what gets attached to an application.
                        It appears only once bought: a download button over
                        nothing would hand back an empty file. */}
                      {vacancy.artifacts?.tailor ? (
                        <>
                          <span className="footer__sep" aria-hidden="true" />
                          <span className="footer__group">
                            <span className="muted">Скачать</span>
                            <DownloadButtons
                              small
                              label={`Скачать резюме под «${vacancy.title}»`}
                              exportBase={tailoredExportBase(vacancy.id)}
                              designerTitle={`Резюме под «${vacancy.title}»`}
                            />
                          </span>
                        </>
                      ) : null}
                    </>
                  }
                />
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="card">
          <Empty title="Пока пусто">
            <p>Отложите вакансию звёздочкой на странице поиска — она появится здесь.</p>
            <a className="btn btn--ghost" href="#/search">
              К поиску
            </a>
          </Empty>
        </div>
      )}

      {panel}
    </div>
  )
}

function ArtifactButton({
  step,
  vacancy,
  busy,
  onClick,
}: {
  step: ApplyStep
  vacancy: FoundVacancy
  busy: boolean
  onClick: () => void
}) {
  const done = Boolean(vacancy.artifacts?.[step.step])
  return (
    <button
      className={done ? 'btn btn--ghost btn--small btn--done' : 'btn btn--quiet btn--small'}
      onClick={onClick}
      disabled={busy}
      title={done ? `${step.hint} — готово` : step.hint}
    >
      {busy ? 'Готовлю…' : done ? `${step.title} ✓` : step.title}
    </button>
  )
}
