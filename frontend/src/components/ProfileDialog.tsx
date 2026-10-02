/** The account: who the user is and the settings they rarely touch.
 *
 * Everything here is visited once a month at most — a name, how to log in,
 * the consent on record — so it lives behind the name in the sidebar rather
 * than taking room beside the pages people work in all day.
 *
 * <dialog>, as the drawer: focus trapping, Escape and inertness for free.
 */

import { useEffect, useRef, useState, useTransition, type FormEvent } from 'react'
import { useSWRConfig } from 'swr'
import { STATE_KEY, deleteAccount, updateProfile } from '../api/hooks'
import type { AppState } from '../api/types'
import { useAuth } from '../auth'
import { LegalLinks } from '../pages/legal/LegalPage'
import { CloseIcon, LogoutIcon } from './icons'
import { TelegramLink } from './TelegramLogin'
import { useToast } from './toast'
import './account.css'

type User = AppState['user']

/** What the sidebar calls the user: their name once given, else how they log in. */
export const displayName = (user: User): string =>
  user.name || user.email || user.telegram_name || 'Без имени'

export function Avatar({ user }: { user: User }) {
  return (
    <span className="avatar" aria-hidden="true">
      {displayName(user).charAt(0).toUpperCase()}
    </span>
  )
}

export function ProfileDialog({
  open,
  user,
  onClose,
}: {
  open: boolean
  user: User
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const { logout } = useAuth()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      className="account"
      aria-label="Профиль"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <header className="account__head">
        <Avatar user={user} />
        <h2 className="account__title">{displayName(user)}</h2>
        <button
          type="button"
          className="btn btn--quiet btn--small btn--icon"
          onClick={onClose}
          aria-label="Закрыть"
          title="Закрыть (Esc)"
        >
          <CloseIcon />
        </button>
      </header>

      <div className="account__body">
        <section className="account__section">
          <label className="eyebrow" htmlFor="account-name">
            Имя
          </label>
          {/* Keyed by the saved value: a save or another tab's change resets
              the draft, instead of a second useState mirroring the server. */}
          <NameForm key={user.name ?? ''} saved={user.name ?? ''} />
        </section>

        {/* A Telegram sign-up has no email: an empty "Почта" row would read as
            something missing that the user is supposed to fill in. */}
        {user.email ? (
          <section className="account__section">
            <p className="eyebrow">Почта для входа</p>
            <p className="account__value">{user.email}</p>
          </section>
        ) : null}

        <section className="account__section">
          <p className="eyebrow">Вход через Telegram</p>
          {/* Email and password come as a pair, so no email means Telegram is
              the only way in — and the server refuses to unlink it. */}
          <TelegramLink
            linkedAs={user.telegram_name ?? null}
            canUnlink={user.email !== null}
          />
        </section>

        <section className="account__section">
          <p className="eyebrow">Персональные данные</p>
          <p className="muted">
            {user.consent_at
              ? `Согласие на обработку дано ${formatDate(user.consent_at)}`
              : 'Согласие ещё не дано.'}
          </p>
          <LegalLinks />
        </section>

        <section className="account__section">
          <p className="eyebrow">Удаление аккаунта</p>
          <DeleteAccount />
        </section>
      </div>

      <footer className="account__foot">
        <button type="button" className="btn btn--quiet btn--small" onClick={logout}>
          <LogoutIcon />
          Выйти
        </button>
      </footer>
    </dialog>
  )
}

function NameForm({ saved }: { saved: string }) {
  const [draft, setDraft] = useState(saved)
  const [isPending, startTransition] = useTransition()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const changed = draft.trim() !== saved

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!changed) return
    startTransition(async () => {
      try {
        await updateProfile({ name: draft })
        await mutate(STATE_KEY)
        toast.info(draft.trim() ? 'Имя сохранено' : 'Имя убрано')
      } catch (e) {
        toast.error(e)
      }
    })
  }

  return (
    <form className="account__line" onSubmit={onSubmit}>
      <input
        id="account-name"
        className="input"
        value={draft}
        maxLength={80}
        placeholder="Как к вам обращаться"
        autoComplete="name"
        onChange={(event) => setDraft(event.target.value)}
      />
      <button className="btn" type="submit" disabled={!changed || isPending}>
        Сохранить
      </button>
    </form>
  )
}

/** Withdrawing consent, in the one form the service can honour it: everything
 *  goes. Two steps, because nothing brings the reports back. */
function DeleteAccount() {
  const { logout } = useAuth()
  const toast = useToast()
  const [armed, setArmed] = useState(false)
  const [isPending, startTransition] = useTransition()

  function onDelete() {
    startTransition(async () => {
      try {
        await deleteAccount()
        logout()
      } catch (e) {
        toast.error(e)
      }
    })
  }

  if (!armed)
    return (
      <>
        <p className="muted">
          Удалит резюме, найденные вакансии, оценки, письма и переписку. Это же — отзыв
          согласия на обработку данных.
        </p>
        <button
          type="button"
          className="btn btn--ghost btn--small account__danger"
          onClick={() => setArmed(true)}
        >
          Удалить аккаунт
        </button>
      </>
    )
  return (
    <>
      <p>
        <b>Удалить всё безвозвратно?</b> Восстановить аккаунт и оценки будет нельзя.
      </p>
      <div className="account__line">
        <button
          type="button"
          className="btn btn--small account__danger account__danger--on"
          onClick={onDelete}
          disabled={isPending}
        >
          {isPending ? 'Удаляю…' : 'Да, удалить'}
        </button>
        <button
          type="button"
          className="btn btn--quiet btn--small"
          onClick={() => setArmed(false)}
          disabled={isPending}
        >
          Отмена
        </button>
      </div>
    </>
  )
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}
