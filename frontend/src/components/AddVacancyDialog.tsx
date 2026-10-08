/** «Добавить» in «Избранное»: a vacancy by its link.
 *
 * Two steps, both free: the link is read and shown, then added. Showing it
 * first lets the user see that the link is the vacancy they meant before it
 * becomes theirs. A paste reads the link at once; a typed one waits for Enter.
 *
 * Only links to our sources are accepted (core/sources/user_added.py). Text
 * is refused here without a request; a link to another site is refused by
 * the server, which never fetches it. Mounted on open, so every opening
 * starts empty.
 */

import { useEffect, useRef, useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, addVacancy, previewVacancy } from '../api/hooks'
import { ApiError } from '../api/client'
import type { VacancyPreview } from '../api/types'
import { CloseIcon } from './icons'
import { useToast } from './toast'
import './add-vacancy.css'

const NOT_ACCEPTED = 'Принимаем ссылки только с проверенных ресурсов'
const LINK_RE = /^https?:\/\/\S+$/i
const FAILED = 'Не получилось загрузить вакансию. Попробуйте ещё раз.'

/** An answer is shown only while the field still holds the link it answers. */
type Answer = { link: string } & (
  { preview: VacancyPreview } | { error: string; retry: boolean }
)

/** Only a 422 carries a reason written for the user (the link is not ours,
 *  the vacancy was taken down). Anything else — a 404 from an API without the
 *  route, a 5xx, no network — says nothing the user can act on but "again". */
function failure(e: unknown): { error: string; retry: boolean } {
  if (e instanceof ApiError && e.status === 422 && e.message) {
    return { error: e.message, retry: false }
  }
  return { error: FAILED, retry: true }
}

export function AddVacancyDialog({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [link, setLink] = useState('')
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [isReading, startReading] = useTransition()
  const [isAdding, startAdding] = useTransition()

  useEffect(() => {
    // jsdom has no showModal; the dialog is then simply not shown modally.
    ref.current?.showModal?.()
    // After showModal, which moves focus to the first button (the cross):
    // React's autoFocus runs before it and sets no attribute to honour.
    input.current?.focus()
  }, [])

  const value = link.trim()
  const current = answer?.link === value ? answer : null
  const preview = current && 'preview' in current ? current.preview : null
  const retry = Boolean(current && 'retry' in current && current.retry)
  const error =
    value && !LINK_RE.test(value)
      ? NOT_ACCEPTED
      : current && 'error' in current
        ? current.error
        : null

  function read(raw: string) {
    const target = raw.trim()
    if (!LINK_RE.test(target)) return
    startReading(async () => {
      try {
        setAnswer({ link: target, preview: await previewVacancy(target) })
      } catch (e) {
        setAnswer({ link: target, ...failure(e) })
      }
    })
  }

  function add(posting: VacancyPreview) {
    startAdding(async () => {
      try {
        await addVacancy(posting.posting_id)
        await mutate(STATE_KEY)
        toast.info('Добавлено в избранное.')
        onClose()
      } catch (e) {
        toast.error(e)
      }
    })
  }

  const busy = isReading || isAdding
  let label = 'Найти'
  if (isReading) label = 'Загружаю…'
  else if (isAdding) label = 'Добавляю…'
  else if (preview?.saved) label = 'Уже в избранном'
  else if (preview) label = 'Добавить'

  return (
    <dialog
      ref={ref}
      className="addvac"
      aria-labelledby="addvac-title"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <form
        className="addvac__form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          if (preview) {
            if (!preview.saved) add(preview)
          } else read(value)
        }}
      >
        <header className="addvac__head">
          <h2 id="addvac-title" className="addvac__title">
            Добавить вакансию
          </h2>
          <button
            type="button"
            className="btn btn--quiet btn--small btn--icon"
            onClick={onClose}
            aria-label="Закрыть"
          >
            <CloseIcon />
          </button>
        </header>

        <input
          className={
            error ? 'input addvac__input addvac__input--bad' : 'input addvac__input'
          }
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="Вставьте ссылку на вакансию"
          aria-label="Ссылка на вакансию"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? 'addvac-error' : undefined}
          ref={input}
          value={link}
          onChange={(e) => setLink(e.target.value)}
          onPaste={(e) => {
            // A paste replaces the field: the link is the whole of it.
            const pasted = e.clipboardData.getData('text')
            if (!pasted) return
            e.preventDefault()
            setLink(pasted.trim())
            read(pasted)
          }}
        />

        {error ? (
          <p id="addvac-error" className="err addvac__error" role="alert">
            {error}
          </p>
        ) : null}

        {isReading ? (
          <div className="addvac__card addvac__card--loading" aria-hidden="true">
            <span className="addvac__bar" />
            <span className="addvac__bar addvac__bar--short" />
          </div>
        ) : preview ? (
          <div className="addvac__card" aria-live="polite">
            <p className="addvac__name">{preview.title}</p>
            {[preview.company, preview.salary_text, preview.location].some(Boolean) ? (
              <p className="addvac__meta">
                {[preview.company, preview.salary_text, preview.location]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            ) : null}
            {preview.closed_reason ? (
              <p className="addvac__closed">Закрыта: {preview.closed_reason}</p>
            ) : null}
          </div>
        ) : null}

        <div className="addvac__actions">
          <button
            type="submit"
            className="btn"
            disabled={
              busy || !value || (Boolean(error) && !retry) || Boolean(preview?.saved)
            }
          >
            {label}
          </button>
        </div>
      </form>
    </dialog>
  )
}
