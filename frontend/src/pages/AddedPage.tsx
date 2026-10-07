/** «Мои вакансии» — vacancies the user brought: a link or a pasted text.
 *
 * The spec's first scenario, "is this one worth it?", for postings no scan
 * found. Kept apart from search: those rows are what the filters picked, these
 * are what the person picked, and "Очистить" or a profile change must not
 * touch them. A star sends one to «Избранное», where it is worked on with the
 * materials and the tracker like any other.
 *
 * One field for both: a lone URL is a link, anything else is the text. Adding
 * is free — the page is read on the server at once, so a link that cannot be
 * read says so now rather than at the paid evaluation.
 */

import { useMemo, useState, useTransition } from 'react'
import { Link } from 'react-router-dom'
import { useSWRConfig } from 'swr'
import {
  STATE_KEY,
  addVacancy,
  deleteVacancy,
  saveVacancy,
  useAppState,
} from '../api/hooks'
import type { FoundVacancy } from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { StarIcon } from '../components/icons'
import { useToast } from '../components/toast'
import { PageHead, Skeleton } from '../components/ui'
import { useDocumentPanel } from '../components/useDocumentPanel'
import { StateError } from '../components/Fallback'
import { VacancyRow } from '../components/VacancyRow'
import './added.css'

/** Newest first: the one just added is the one the user is looking for. */
const byAdded = (a: FoundVacancy, b: FoundVacancy) =>
  (b.added_at ?? '').localeCompare(a.added_at ?? '')

export function AddedPage() {
  const { data, isLoading, error } = useAppState()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const { openReport, isBusy, panel } = useDocumentPanel()
  const [source, setSource] = useState('')
  const [isAdding, startAdding] = useTransition()
  const [deleting, setDeleting] = useState<FoundVacancy | null>(null)

  const found = data?.found
  const added = useMemo(
    () => (found ?? []).filter((row) => row.added_at).sort(byAdded),
    [found],
  )

  function add() {
    const value = source.trim()
    if (!value) return
    startAdding(async () => {
      try {
        const result = await addVacancy(value)
        await mutate(STATE_KEY)
        setSource('')
        toast.info(
          result.created
            ? 'Вакансия добавлена.'
            : 'Эта вакансия у вас уже была — теперь она здесь.',
        )
      } catch (e) {
        toast.error(e)
      }
    })
  }

  async function toggleSave(vacancy: FoundVacancy) {
    try {
      await saveVacancy(vacancy.id, !vacancy.saved)
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

  if (error) return <StateError error={error} />
  if (isLoading || !data)
    return (
      <div className="page">
        <Skeleton rows={4} />
      </div>
    )

  return (
    <div className="page">
      <PageHead title="Мои вакансии" />

      <form
        className="card addform"
        onSubmit={(e) => {
          e.preventDefault()
          add()
        }}
      >
        <label className="sr-only" htmlFor="added-source">
          Ссылка на вакансию или её текст
        </label>
        <textarea
          id="added-source"
          className="input addform__area"
          rows={4}
          value={source}
          placeholder="Ссылка на вакансию или её текст"
          onChange={(e) => setSource(e.target.value)}
          onKeyDown={(e) => {
            // Enter alone is a line break in a pasted text; Ctrl+Enter adds.
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              add()
            }
          }}
        />
        <div className="addform__foot">
          <button className="btn" type="submit" disabled={isAdding || !source.trim()}>
            {isAdding ? 'Читаю…' : 'Добавить'}
          </button>
        </div>
      </form>

      {added.length ? (
        <div className="vlist">
          {added.map((vacancy) => (
            <VacancyRow
              key={vacancy.id}
              vacancy={vacancy}
              actions={
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
                      : 'В избранное: материалы и статусы готовятся там'
                  }
                >
                  <StarIcon filled={vacancy.saved} />
                </button>
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
                  <Link
                    className="btn btn--quiet btn--small"
                    to={`/chat?vacancy=${vacancy.id}`}
                    title="Обсудить вакансию с помощником"
                  >
                    Обсудить
                  </Link>
                  <button
                    className="btn btn--quiet btn--small"
                    onClick={() => setDeleting(vacancy)}
                    title="Удалить вакансию вместе с отчётом и материалами"
                  >
                    Удалить
                  </button>
                </>
              }
            />
          ))}
        </div>
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        title="Удалить вакансию?"
        confirmLabel="Удалить"
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      >
        <p>«{deleting?.title}» исчезнет из «Моих вакансий» и из избранного.</p>
        {deleting?.score != null ? (
          <p className="muted">Отчёт и подготовленные материалы удалятся вместе с ней.</p>
        ) : null}
      </ConfirmDialog>

      {panel}
    </div>
  )
}
