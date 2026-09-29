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
import { STATE_KEY, updateProfile } from '../api/hooks'
import type { AppState } from '../api/types'
import { useAuth } from '../auth'
import { CloseIcon, LogoutIcon } from './icons'
import { TelegramLink } from './TelegramLogin'
import { useToast } from './toast'
import './profile.css'

type User = AppState['user']

/** What the sidebar calls the user: their name once given, the email until then. */
export const displayName = (user: User): string => user.name || user.email

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
      className="profile"
      aria-label="Профиль"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
    >
      <header className="profile__head">
        <Avatar user={user} />
        <h2 className="profile__title">{displayName(user)}</h2>
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

      <div className="profile__body">
        <section className="profile__section">
          <label className="eyebrow" htmlFor="profile-name">
            Имя
          </label>
          {/* Keyed by the saved value: a save or another tab's change resets
              the draft, instead of a second useState mirroring the server. */}
          <NameForm key={user.name ?? ''} saved={user.name ?? ''} />
        </section>

        <section className="profile__section">
          <p className="eyebrow">Почта для входа</p>
          <p className="profile__value">{user.email}</p>
        </section>

        <section className="profile__section">
          <p className="eyebrow">Вход через Telegram</p>
          <TelegramLink linkedAs={user.telegram_name ?? null} />
        </section>

        <section className="profile__section">
          <p className="eyebrow">Персональные данные</p>
          <p className="muted">
            {user.consent_at
              ? `Согласие на обработку дано ${formatDate(user.consent_at)}`
              : 'Согласие ещё не дано — его спросят при загрузке резюме.'}
          </p>
        </section>
      </div>

      <footer className="profile__foot">
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
        toast.info(draft.trim() ? 'Имя сохранено' : 'Имя убрано — показываем почту')
      } catch (e) {
        toast.error(e)
      }
    })
  }

  return (
    <form className="profile__line" onSubmit={onSubmit}>
      <input
        id="profile-name"
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

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}
