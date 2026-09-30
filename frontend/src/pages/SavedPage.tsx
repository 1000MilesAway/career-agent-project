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
 */

import { useMemo, useState, useTransition } from 'react'
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
import { Empty, ErrorNote, PageHead, Skeleton, rub } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import './saved.css'

/** `null` is "all". */
type StatusFilter = string | null

export function SavedPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, openArtifact, isBusy, panel } = useDocumentPanel()
  const [filter, setFilter] = useState<StatusFilter>(null)

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
              return (
                <button
                  key={key}
                  className={
                    status.terminal ? 'funnel__step funnel__step--end' : 'funnel__step'
                  }
                  aria-pressed={active === key}
                  disabled={!count}
                  onClick={() => setFilter(active === key ? null : key)}
                >
                  <span className="funnel__count">{count}</span>
                  <span className="funnel__label">{status.title}</span>
                </button>
              )
            })}
          </div>

          <div className="vlist">
            {shown.map((vacancy) => (
              <VacancyRow
                key={vacancy.id}
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

                    {/* One button per document, each stating its own price. A
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
      title={
        done ? `${step.hint} — готово, показ бесплатный` : `${step.hint} — ${rub(step.rub)}`
      }
    >
      {busy ? 'Готовлю…' : done ? `${step.title} ✓` : step.title}
      {!busy && !done ? <span className="btn__price">{rub(step.rub)}</span> : null}
    </button>
  )
}
