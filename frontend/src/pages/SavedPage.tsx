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
 * «Добавить» brings a vacancy by its link (`AddVacancyDialog`). Such a row
 * has no scan to bring it back, so its cross deletes it, after asking —
 * unsaved, it would show nowhere.
 *
 * A row can be dragged onto a stage of the funnel to move it there (see
 * `useStageDrag`). The select in the row stays: it is the keyboard's way in,
 * and the only one for a row nobody can see the funnel from.
 */

import { useMemo, useState, useTransition } from 'react'
import { Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  deleteVacancy,
  tailoredExportBase,
  recheckVacancies,
  saveVacancy,
  moveVacancy,
  useAppState,
} from '../api/hooks'
import type { ApplyStep, FoundVacancy } from '../api/types'
import { AddVacancyDialog } from '../components/AddVacancyDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DownloadButtons } from '../components/DownloadButtons'
import { VacancyRow } from '../components/VacancyRow'
import { CloseIcon, PlusIcon } from '../components/icons'
import { useToast } from '../components/toast'
import { Empty, PageHead, Skeleton } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import { StateError } from '../components/Fallback'
import './saved.css'
import { GRIP, useStageDrag } from './useStageDrag'

/** `null` is "all". */
type StatusFilter = string | null

export function SavedPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, openArtifact, isBusy, panel } = useDocumentPanel()
  const [filter, setFilter] = useState<StatusFilter>(null)
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<FoundVacancy | null>(null)

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

  async function remove() {
    const vacancy = deleting
    setDeleting(null)
    if (!vacancy) return
    try {
      await deleteVacancy(vacancy.id)
      await mutate(STATE_KEY)
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

  const { held, over, ghostRef, onPointerDown } = useStageDrag((id, stage) => {
    const row = saved.find((r) => r.id === id)
    if (row && (row.status ?? 'none') !== stage) void move(id, stage)
  })
  const carried = held ? saved.find((r) => r.id === held.id) : undefined

  if (error) return <StateError error={error} />
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
        actions={
          <>
            {saved.length ? (
              <button
                className="btn btn--ghost btn--small"
                onClick={recheck}
                disabled={isRechecking}
                title="Спросить у Хабра, geekjob и Telegram, не закрыты ли вакансии. Бесплатно, модель не вызывается. Вакансии с сайтов работодателей так проверить нельзя."
              >
                {isRechecking ? 'Проверяю…' : 'Проверить актуальность'}
              </button>
            ) : null}
            <button className="btn btn--small" onClick={() => setAdding(true)}>
              <PlusIcon />
              Добавить
            </button>
          </>
        }
      />

      {saved.length ? (
        <>
          <div
            className={carried ? 'funnel funnel--live' : 'funnel'}
            role="group"
            aria-label="Этап отклика"
          >
            {/* "All" is a step of its own: without it the unfiltered list
                looked like the first stage, and a row in «Отказ» seemed to
                sit under «Не решено» until that filter was pressed. Not a
                drop target — "all" is not a status. */}
            <button
              className="funnel__step funnel__step--all"
              aria-pressed={active === null}
              onClick={() => setFilter(null)}
            >
              <span className="funnel__count">{saved.length}</span>
              <span className="funnel__label">Все</span>
            </button>
            {Object.entries(data.statuses).map(([key, status]) => {
              const count = counts.get(key) ?? 0
              const classes = ['funnel__step']
              if (status.terminal) classes.push('funnel__step--end')
              if (carried) {
                classes.push('funnel__step--target')
                if ((carried.status ?? 'none') === key)
                  classes.push('funnel__step--current')
              }
              if (carried && over === key && (carried.status ?? 'none') !== key)
                classes.push('funnel__step--over')
              return (
                <button
                  key={key}
                  className={classes.join(' ')}
                  aria-pressed={active === key}
                  data-stage={key}
                  disabled={!count}
                  onClick={() => setFilter(active === key ? null : key)}
                >
                  <span className="funnel__count">{count}</span>
                  <span className="funnel__label">{status.title}</span>
                </button>
              )
            })}
          </div>

          <p className="muted funnel__hint">
            {carried
              ? 'Отпустите на этапе, куда перенести. Esc — отмена.'
              : 'Вакансию можно перетащить на этап: зажмите строку и ведите вверх.'}
          </p>

          <div className="vlist">
            {shown.map((vacancy) => (
              <div
                key={vacancy.id}
                className={carried?.id === vacancy.id ? 'drag drag--active' : 'drag'}
                onPointerDown={(e) => onPointerDown(e, vacancy.id)}
              >
                <VacancyRow
                  vacancy={vacancy}
                  actions={
                    <>
                      {/* For every row, evaluated or not: a row dragged to a
                          stage has a status, and hiding it made the row look
                          undecided. */}
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
                      <button
                        className="btn btn--quiet btn--small btn--icon"
                        onClick={() =>
                          vacancy.added_at ? setDeleting(vacancy) : unsave(vacancy.id)
                        }
                        title={vacancy.added_at ? 'Удалить' : 'Убрать из избранного'}
                        aria-label={
                          vacancy.added_at
                            ? `Удалить «${vacancy.title}»`
                            : `Убрать «${vacancy.title}» из избранного`
                        }
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
                      {/* The chat opens with this vacancy attached to the next
                        message: "what do I answer this recruiter" needs to
                        know which posting, and a company name typed in a
                        sentence is a guess. */}
                      <Link
                        className="btn btn--quiet btn--small"
                        to={`/chat?vacancy=${vacancy.id}`}
                        title="Обсудить вакансию с помощником"
                      >
                        Обсудить
                      </Link>
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
                          <DownloadButtons
                            small
                            label={`Скачать резюме под «${vacancy.title}»`}
                            exportBase={tailoredExportBase(vacancy.id)}
                            designerTitle={`Резюме под «${vacancy.title}»`}
                          />
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
            <p>Отложите вакансию звёздочкой на странице поиска или добавьте по ссылке.</p>
            <Link className="btn btn--ghost" to="/search">
              К поиску
            </Link>
          </Empty>
        </div>
      )}

      {carried && held
        ? createPortal(
            <div
              ref={ghostRef}
              className="drag-ghost"
              aria-hidden="true"
              style={{
                transform: `translate(${held.x - GRIP.x}px, ${held.y - GRIP.y}px)`,
              }}
            >
              <div className="drag-ghost__card">
                <span className="drag-ghost__title">{carried.title}</span>
                <span className="drag-ghost__meta">
                  {[carried.company, carried.salary_text].filter(Boolean).join(' · ') ||
                    'компания не указана'}
                </span>
              </div>
            </div>,
            document.body,
          )
        : null}

      {adding ? <AddVacancyDialog onClose={() => setAdding(false)} /> : null}

      <ConfirmDialog
        open={deleting !== null}
        title="Удалить вакансию?"
        confirmLabel="Удалить"
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      >
        <p>«{deleting?.title}» исчезнет из избранного.</p>
        {deleting?.score != null ? (
          <p className="muted">Отчёт и подготовленные материалы удалятся вместе с ней.</p>
        ) : null}
      </ConfirmDialog>

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
