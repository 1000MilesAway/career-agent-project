/** Источники: where vacancies come from, and the owner's switches over them.
 *
 * Aggregators are searched by query and only have numbers here. Employer
 * boards, Telegram channels (Tg public, read through t.me/s/) and Telegram
 * chats (Telethon, read by the tg-provider userbot) can be hidden from every
 * user's results; chats are also added here, and a chat switched off is no
 * longer scanned. Any
 * company name a board, channel or chat carries can be shown under another one
 * ("ПАО Сбербанк" as "Сбербанк"). The tab and the search live in the URL,
 * like the other dashboard pages.
 */

import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { addChat, setCompanyName, setSourceHidden, useAdminSources } from '../api/hooks'
import type {
  AdminBoard,
  AdminChannel,
  AdminChat,
  AdminCompanyName,
  AdminSources,
  ChannelGroup,
  SourceStats,
} from '../api/types'
import { useToast } from '../components/toast'
import { Empty, ErrorNote, PageHead, Skeleton } from '../components/ui'
import { formatCount, formatDateTime } from './adminFormat'
import './admin.css'

type Tab = 'aggregators' | 'boards' | 'channels' | 'chats'

const TABS: { value: Tab; label: string; count: (d: AdminSources) => number }[] = [
  { value: 'aggregators', label: 'Агрегаторы', count: (d) => d.aggregators.length },
  { value: 'boards', label: 'Сайты компаний', count: (d) => d.boards.length },
  { value: 'channels', label: 'Tg public', count: (d) => d.channels.length },
  { value: 'chats', label: 'Telethon', count: (d) => d.chats.length },
]

const BOARD_GROUPS: Record<string, string> = { ru: 'РФ', global: 'зарубежные' }

const CHANNEL_GROUPS: Record<ChannelGroup, string> = {
  core: 'основные',
  adjacent: 'смежные',
  special: 'не читается',
}

const matches = (needle: string, ...values: (string | null | undefined)[]) =>
  !needle || values.some((v) => v?.toLocaleLowerCase('ru').includes(needle))

export function AdminSourcesPage() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.find((t) => t.value === params.get('tab'))?.value ?? 'aggregators'
  const q = params.get('q') ?? ''
  const needle = q.trim().toLocaleLowerCase('ru')
  const { data, error, isLoading } = useAdminSources()

  function update(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }

  return (
    <div className="page page--wide">
      <PageHead title="Источники" />

      <div className="admin-toolbar">
        <div className="seg" role="group" aria-label="Раздел">
          {TABS.map((option) => (
            <button
              key={option.value}
              type="button"
              className="seg__item"
              aria-pressed={tab === option.value}
              onClick={() =>
                update('tab', option.value === 'aggregators' ? null : option.value)
              }
            >
              {option.label}
              {data ? <span className="seg__count">{option.count(data)}</span> : null}
            </button>
          ))}
        </div>
        {tab === 'aggregators' ? null : (
          <input
            type="search"
            className="input admin-toolbar__search"
            placeholder={tab === 'boards' ? 'Компания' : tab === 'chats' ? 'Чат' : 'Канал'}
            aria-label="Поиск по источникам"
            value={q}
            onChange={(event) => update('q', event.target.value || null)}
          />
        )}
      </div>

      <ErrorNote error={error} />

      {isLoading && !data ? (
        <Skeleton rows={6} />
      ) : !data ? null : tab === 'aggregators' ? (
        <Aggregators data={data} />
      ) : tab === 'boards' ? (
        <Boards
          boards={data.boards.filter(
            (b) =>
              matches(needle, b.label, b.key) ||
              b.names.some((n) => matches(needle, n.name, n.display_name)),
          )}
        />
      ) : tab === 'chats' ? (
        <Chats
          data={data}
          chats={data.chats.filter(
            (c) =>
              matches(needle, c.name) ||
              c.names.some((n) => matches(needle, n.name, n.display_name)),
          )}
        />
      ) : (
        <Channels
          channels={data.channels.filter(
            (c) =>
              matches(needle, c.name) ||
              c.names.some((n) => matches(needle, n.name, n.display_name)),
          )}
        />
      )}
    </div>
  )
}

function Numbers({ stats }: { stats: SourceStats }) {
  return (
    <>
      <td className="table__num">{formatCount(stats.postings)}</td>
      <td className="table__num">{formatCount(stats.active)}</td>
    </>
  )
}

function NumberHeads() {
  return (
    <>
      <th scope="col" className="table__num">
        Вакансий
      </th>
      <th scope="col" className="table__num" title="Открыты и видны поиску">
        Актуальных
      </th>
    </>
  )
}

function Aggregators({ data }: { data: AdminSources }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Источник</th>
            <NumberHeads />
            <th scope="col">Последнее чтение</th>
          </tr>
        </thead>
        <tbody>
          {data.aggregators.map((item) => (
            <tr key={item.source}>
              <td>
                <b>{item.label}</b>
              </td>
              <Numbers stats={item} />
              <td className="table__date">
                {item.fetched_at ? formatDateTime(item.fetched_at) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Boards({ boards }: { boards: AdminBoard[] }) {
  if (boards.length === 0) return <Empty title="Ничего не нашлось" />
  return (
    <div className="table-wrap">
      <table className="table sources">
        <thead>
          <tr>
            <th scope="col">Компания</th>
            <NumberHeads />
            <th scope="col">В выдаче</th>
          </tr>
        </thead>
        {boards.map((board) => (
          <tbody key={board.key} className={board.hidden ? 'sources__off' : undefined}>
            <tr>
              <td>
                <div className="who">
                  <span className="who__main">{board.label}</span>
                  <span className="who__sub">
                    {[board.key, BOARD_GROUPS[board.group]].filter(Boolean).join(' · ')}
                  </span>
                </div>
              </td>
              <Numbers stats={board} />
              <td>
                <Shown
                  label={board.label}
                  hidden={board.hidden}
                  onChange={(hidden) => setSourceHidden('company', board.key, hidden)}
                />
              </td>
            </tr>
            {board.names.map((name) => (
              <tr key={name.name} className="sources__name">
                <td>
                  <NameField item={name} />
                </td>
                <Numbers stats={name} />
                <td />
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  )
}

function Channels({ channels }: { channels: AdminChannel[] }) {
  if (channels.length === 0) return <Empty title="Ничего не нашлось" />
  return (
    <div className="table-wrap">
      <table className="table sources">
        <thead>
          <tr>
            <th scope="col">Канал</th>
            <th scope="col">Группа</th>
            <NumberHeads />
            <th scope="col">В выдаче</th>
          </tr>
        </thead>
        {channels.map((channel) => (
          <tbody key={channel.key} className={channel.hidden ? 'sources__off' : undefined}>
            <tr>
              <td>
                <a href={`https://t.me/s/${channel.key}`} target="_blank" rel="noreferrer">
                  {channel.name}
                </a>
              </td>
              <td className="muted">
                {channel.group ? CHANNEL_GROUPS[channel.group] : 'вне списка'}
              </td>
              <Numbers stats={channel} />
              <td>
                <Shown
                  label={channel.name}
                  hidden={channel.hidden}
                  onChange={(hidden) => setSourceHidden('telegram', channel.key, hidden)}
                />
              </td>
            </tr>
            <Names names={channel.names} />
          </tbody>
        ))}
      </table>
    </div>
  )
}

function Chats({ data, chats }: { data: AdminSources; chats: AdminChat[] }) {
  return (
    <>
      <AddChat />
      <ErrorNote error={data.chats_error} />
      {data.chats_scanned_at ? (
        <p className="muted sources__note">
          Последний скан: {formatDateTime(data.chats_scanned_at)}
        </p>
      ) : null}
      {chats.length === 0 ? (
        <Empty title="Чатов нет" />
      ) : (
        <div className="table-wrap">
          <table className="table sources">
            <thead>
              <tr>
                <th scope="col">Чат</th>
                <th scope="col">Последний скан</th>
                <NumberHeads />
                <th scope="col">Читается</th>
              </tr>
            </thead>
            {chats.map((chat) => (
              <tbody key={chat.key} className={chat.hidden ? 'sources__off' : undefined}>
                <tr>
                  <td>
                    {/^-?\d+$/.test(chat.key) ? (
                      chat.name
                    ) : (
                      <a href={`https://t.me/${chat.key}`} target="_blank" rel="noreferrer">
                        {chat.name}
                      </a>
                    )}
                  </td>
                  <td className={chat.error ? 'err' : 'muted'}>
                    {chat.error
                      ? `ошибка: ${chat.error}`
                      : chat.emitted == null
                        ? '—'
                        : `сообщений: ${formatCount(chat.emitted)}`}
                  </td>
                  <Numbers stats={chat} />
                  <td>
                    <Shown
                      label={chat.name}
                      hidden={chat.hidden}
                      on="включён"
                      off="выключен"
                      onChange={(hidden) => setSourceHidden('telethon', chat.key, hidden)}
                    />
                  </td>
                </tr>
                <Names names={chat.names} />
              </tbody>
            ))}
          </table>
        </div>
      )}
    </>
  )
}

function AddChat() {
  const toast = useToast()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!value.trim() || busy) return
    setBusy(true)
    try {
      const key = await addChat(value.trim())
      toast.info(`Чат @${key} добавлен`)
      setValue('')
    } catch (error) {
      toast.error(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="admin-toolbar" onSubmit={submit}>
      <input
        className="input admin-toolbar__search"
        placeholder="@chat, ссылка t.me или id группы"
        aria-label="Добавить чат"
        value={value}
        disabled={busy}
        onChange={(event) => setValue(event.target.value)}
      />
      <button type="submit" className="btn btn--small" disabled={busy || !value.trim()}>
        Добавить чат
      </button>
    </form>
  )
}

/** Employers a channel's or chat's posts name, each renameable. */
function Names({ names }: { names: AdminCompanyName[] }) {
  return names.map((name) => (
    <tr key={name.name} className="sources__name">
      <td>
        <NameField item={name} />
      </td>
      <td />
      <Numbers stats={name} />
      <td />
    </tr>
  ))
}

function Shown({
  label,
  hidden,
  onChange,
  on = 'показан',
  off = 'скрыт',
}: {
  label: string
  hidden: boolean
  onChange: (hidden: boolean) => Promise<void>
  on?: string
  off?: string
}) {
  const toast = useToast()
  return (
    <label className="toggle">
      <input
        type="checkbox"
        checked={!hidden}
        aria-label={`Показывать пользователям: ${label}`}
        onChange={(event) => onChange(!event.target.checked).catch(toast.error)}
      />
      {hidden ? off : on}
    </label>
  )
}

/** The name as the source wrote it, and an input for what users see. Saved
 *  on Enter or when the field loses focus; emptied — the original is back. */
function NameField({ item, link }: { item: AdminCompanyName; link?: string }) {
  const saved = item.display_name ?? ''
  // Keyed by the saved value: a save or another tab's change resets the draft.
  return <NameInput key={saved} item={item} saved={saved} link={link} />
}

function NameInput({
  item,
  saved,
  link,
}: {
  item: AdminCompanyName
  saved: string
  link?: string
}) {
  const toast = useToast()
  const [draft, setDraft] = useState(saved)
  const [busy, setBusy] = useState(false)

  async function commit() {
    if (draft.trim() === saved || busy) return
    setBusy(true)
    try {
      await setCompanyName(item.name, draft)
    } catch (error) {
      toast.error(error)
      setDraft(saved)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sources__rename">
      <span className="sources__original">
        {link ? (
          <a href={link} target="_blank" rel="noreferrer">
            {item.name}
          </a>
        ) : (
          item.name
        )}
      </span>
      <input
        className="input sources__input"
        value={draft}
        placeholder={item.name}
        aria-label={`Название для пользователей: ${item.name}`}
        disabled={busy}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur()
          if (event.key === 'Escape') setDraft(saved)
        }}
      />
    </div>
  )
}
