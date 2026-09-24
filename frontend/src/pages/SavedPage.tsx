/** "Избранное" — what the user actually works on.
 *
 * Materials and the tracker live here and nowhere else: the search page
 * decides what is worth keeping, this one does the work on it. Clearing the
 * search list never touches these rows — they carry reports and documents
 * that were paid for.
 */

import { useMemo, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  downloadTailored,
  recheckVacancies,
  setSaved,
  setStatus,
  useAppState,
} from '../api/hooks'
import type { ApplyStep, FoundVacancy } from '../api/types'
import { DownloadButtons } from '../components/DownloadButtons'
import { VacancyRow } from '../components/VacancyRow'
import { useToast } from '../components/toast'
import { Card, ErrorNote, Skeleton, rub } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'

export function SavedPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, openArtifact, isBusy, panel } = useDocumentPanel()

  const found = data?.found
  const saved = useMemo(() => found?.filter((row) => row.saved) ?? [], [found])
  const [isRechecking, startRecheck] = useTransition()

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
      await setSaved(id, false)
      await mutate(STATE_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  async function move(id: string, status: string) {
    try {
      await setStatus(id, status)
      await mutate(STATE_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  if (error) return <ErrorNote error={error} />
  if (isLoading || !data) return <Skeleton rows={4} />

  return (
    <>
      <Card title="Избранное">
        <p className="muted" style={{ marginTop: 0 }}>
          Вакансии, которые вы отложили. Здесь готовятся материалы к отклику и отмечается,
          на каком этапе вы находитесь. Очистка поиска это не трогает.
        </p>

        {saved.length ? (
          <div className="row">
            <p className="muted">
              В работе <b>{saved.length}</b>.
            </p>
            <button
              className="btn btn--ghost btn--small"
              onClick={recheck}
              disabled={isRechecking}
              title="Спросить у Хабра и geekjob, не закрыты ли вакансии. Бесплатно, модель не вызывается. Telegram так проверить нельзя — у поста нет страницы."
            >
              {isRechecking ? 'Проверяю…' : 'Проверить, открыты ли'}
            </button>
          </div>
        ) : (
          <p className="muted">
            Пусто. Отложите вакансию кнопкой «В избранное» на странице поиска.
          </p>
        )}

        {saved.map((vacancy) => (
          <VacancyRow
            key={vacancy.id}
            vacancy={vacancy}
            actions={
              <>
                <button
                  className="btn btn--ghost btn--small"
                  onClick={() => openReport(vacancy)}
                  disabled={isBusy(`${vacancy.id}:report`)}
                >
                  {isBusy(`${vacancy.id}:report`)
                    ? 'Считаю…'
                    : vacancy.score != null
                      ? 'Отчёт'
                      : 'Оценить'}
                </button>
                {vacancy.outdated ? (
                  <button
                    className="btn btn--ghost btn--small"
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
                {vacancy.score != null
                  ? data.apply_steps.map((step) => (
                      <ArtifactButton
                        key={step.step}
                        step={step}
                        vacancy={vacancy}
                        busy={isBusy(`${vacancy.id}:${step.step}`)}
                        onClick={() => openArtifact(vacancy, step.step)}
                      />
                    ))
                  : null}

                {/* The tailored CV is the one document that leaves the
                    cabinet — it is what gets attached to an application. It
                    appears only once bought: a download button over nothing
                    would hand back an empty file. */}
                {vacancy.artifacts?.tailor ? (
                  <DownloadButtons
                    small
                    label={`Скачать резюме под «${vacancy.title}»`}
                    onDownload={(fmt) => downloadTailored(vacancy.id, fmt)}
                  />
                ) : null}

                {vacancy.score != null ? (
                  <>
                    <label className="sr-only" htmlFor={`status-${vacancy.id}`}>
                      Статус отклика
                    </label>
                    <select
                      id={`status-${vacancy.id}`}
                      className="select"
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
                  className="btn btn--ghost btn--small"
                  onClick={() => unsave(vacancy.id)}
                  title="Убрать из избранного"
                  aria-label={`Убрать «${vacancy.title}» из избранного`}
                >
                  ✕
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
      className={done ? 'btn btn--ghost btn--small btn--done' : 'btn btn--ghost btn--small'}
      onClick={onClick}
      disabled={busy}
      title={
        done ? `${step.hint} — готово, показ бесплатный` : `${step.hint} — ${rub(step.rub)}`
      }
    >
      {busy ? 'Готовлю…' : done ? `${step.title} ✓` : step.title}
    </button>
  )
}
