/** "Помощник" — the chat.
 *
 * Several conversations, listed in the sidebar's place (a window on a phone): one
 * endless thread mixed CV edits, three vacancies and a reply to an employer,
 * and nobody could find anything in it. A vacancy has its own thread, which
 * "Обсудить" opens.
 *
 * In a thread: the conversation, the pending changes, the input. The changes
 * sit under the last message rather than in a side panel, because they are
 * the answer to what was just said.
 *
 * Nothing here writes to the profile. Every change goes through a card with
 * two buttons, and closing the page decides nothing — which is the product
 * rule this screen exists to make visible.
 *
 * `/chat` opens the latest thread; `/chat/new` is a thread not made yet — it
 * is created by its first message, so pressing "Новый разговор" and leaving
 * leaves no empty thread behind.
 */

import { useEffect, useRef, useState } from 'react'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useSWRConfig } from 'swr'
import {
  PROPOSALS_KEY,
  STATE_KEY,
  THREADS_KEY,
  acceptProposal,
  createThread,
  deleteThread,
  rejectProposal,
  renameThread,
  sendMessage,
  threadKey,
  useAppState,
  useChat,
  useProposals,
  useThreads,
} from '../api/hooks'
import type { ChatThread, VacancyCard, VacancyRef } from '../api/types'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Drawer } from '../components/drawer'
import { ProposalCard } from '../components/ProposalCard'
import { copySelection } from '../components/report'
import { useToast } from '../components/toast'
import {
  ArrowUpIcon,
  BrandMark,
  CopyIcon,
  ExternalIcon,
  MoreIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from '../components/icons'
import { ErrorNote, Skeleton } from '../components/ui'
import '../components/chat.css'

/** What each tool is called when the chat says what it looked at. The names
 *  are internal; "посмотрел резюме" is what actually happened. */
const TOOL_TITLES: Record<string, string> = {
  get_profile: 'посмотрел резюме',
  get_vacancy: 'открыл вакансию',
  list_vacancies: 'просмотрел список вакансий',
  skill_gap: 'разобрал требования',
  open_gaps: 'сверил с купленными отчётами',
  get_spend: 'проверил лимиты',
  propose_profile_patch: 'подготовил правку резюме',
  set_vacancy_status: 'подготовил смену статуса',
  save_vacancy: 'подготовил избранное',
}

/** An empty thread opens with a question, not with sample messages: worded
 *  examples ("Мне ответили из Ромашки…") read as a script to follow and
 *  narrowed what people thought the assistant was for. One is picked per
 *  visit, so the screen is not the same every time. */
const GREETINGS = [
  'Что обсудим сегодня?',
  'Чем помочь?',
  'С чего начнём?',
  'Что у вас на уме?',
  'Над чем поработаем?',
  'Как продвигается поиск?',
]

const VACANCY_GREETINGS = [
  'Что обсудим по этой вакансии?',
  'Чем помочь с этой вакансией?',
  'С чего начнём?',
  'Что хотите узнать о вакансии?',
]

const NEW = 'new'

export function ChatPage() {
  const { threadId } = useParams()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const vacancyParam = params.get('vacancy')
  const { data: threads, error: threadsError } = useThreads()

  // "Обсудить" links here with `?vacancy=<id>`: the vacancy's thread is found
  // or made, and the URL replaced with it. An action on arrival, not a read —
  // the thread may not exist until this call makes it.
  const toast = useToast()
  const { mutate } = useSWRConfig()
  const opening = useRef<string | null>(null)
  useEffect(() => {
    if (!vacancyParam || opening.current === vacancyParam) return
    opening.current = vacancyParam
    createThread(vacancyParam).then(
      // The list first: a thread missing from it reads as deleted, and the
      // page would send the user to the latest one instead.
      async (thread) => {
        await mutate(THREADS_KEY)
        navigate(`/chat/${thread.id}`, { replace: true })
      },
      (e) => {
        toast.error(e)
        navigate('/chat', { replace: true })
      },
    )
  }, [vacancyParam, navigate, toast, mutate])

  if (vacancyParam) return <Waiting />

  if (!threadId) {
    if (threadsError)
      return (
        <div className="page">
          <ErrorNote error={threadsError} />
        </div>
      )
    if (!threads) return <Waiting />
    return <Navigate to={`/chat/${threads[0]?.id ?? NEW}`} replace />
  }

  const current = threads?.find((t) => t.id === threadId)
  // A stale link to a deleted thread: back to the list rather than an error.
  if (threads && threadId !== NEW && !current) return <Navigate to="/chat" replace />

  return (
    <div className="page chatpage">
      <ThreadList threads={threads ?? []} currentId={threadId} />
      {/* Keyed by thread: the draft, the attached question and the scroll
          belong to one conversation and must not leak into the next. */}
      <Conversation key={threadId} thread={current ?? null} waiting={!threads} />
    </div>
  )
}

function Waiting() {
  return (
    <div className="page">
      <Skeleton rows={3} />
    </div>
  )
}

/** What a row's menu does, owned by the list: the confirmation and the
 *  rename outlive the menu that started them. */
interface ThreadActions {
  menuFor: string | null
  setMenuFor: (id: string | null) => void
  renaming: { id: string; text: string } | null
  setRenaming: (value: { id: string; text: string } | null) => void
  rename: () => void
  askDelete: (thread: ChatThread) => void
}

function ThreadList({ threads, currentId }: { threads: ChatThread[]; currentId: string }) {
  const navigate = useNavigate()
  const { mutate } = useSWRConfig()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ id: string; text: string } | null>(null)
  const [deleting, setDeleting] = useState<ChatThread | null>(null)
  const current = threads.find((t) => t.id === currentId)
  const go = (id: string) => {
    setOpen(false)
    navigate(`/chat/${id}`)
  }
  const pendingElsewhere = threads.reduce(
    (n, t) => n + (t.id === currentId ? 0 : t.pending),
    0,
  )

  async function rename() {
    if (!renaming) return
    const { id, text } = renaming
    setRenaming(null)
    const title = text.trim()
    const thread = threads.find((t) => t.id === id)
    if (!thread || !title || title === thread.title) return
    try {
      await renameThread(id, title)
      await mutate(THREADS_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  async function remove() {
    const thread = deleting
    setDeleting(null)
    if (!thread) return
    try {
      const out = await deleteThread(thread.id)
      await Promise.all([mutate(THREADS_KEY), mutate(PROPOSALS_KEY)])
      // Another thread deleted from the list leaves the open one where it is.
      if (thread.id === currentId) navigate('/chat', { replace: true })
      toast.info(
        out.proposals
          ? `Разговор удалён, ${out.proposals} неподтверждённых правок отброшено`
          : 'Разговор удалён',
      )
    } catch (e) {
      toast.error(e)
    }
  }

  const actions: ThreadActions = {
    menuFor,
    setMenuFor,
    renaming,
    setRenaming,
    rename,
    askDelete: setDeleting,
  }

  return (
    <>
      <nav className="threads" aria-label="Разговоры">
        <ThreadGroups
          threads={threads}
          currentId={currentId}
          onPick={go}
          actions={actions}
        />
      </nav>

      {/* A phone has no room for the panel: the current thread's name opens
          the same list in a window. */}
      <div className="threads__bar">
        <button className="threads__switch" onClick={() => setOpen(true)}>
          <span className="threads__title">
            {current ? titleOf(current) : 'Новый разговор'}
          </span>
          {pendingElsewhere ? (
            <span className="threads__badge">{pendingElsewhere}</span>
          ) : null}
          <span className="threads__caret" aria-hidden="true" />
        </button>
        <button
          className="btn btn--small"
          onClick={() => go(NEW)}
          disabled={currentId === NEW}
          aria-label="Новый разговор"
          title="Новый разговор"
        >
          <PlusIcon />
        </button>
      </div>
      <Drawer open={open} title="Разговоры" onClose={() => setOpen(false)}>
        {/* Only while open: the same rows as the panel's, and a menu opened on
            one of them would open on its hidden twin as well. */}
        {open ? (
          <div className="threads threads--sheet">
            <ThreadGroups
              threads={threads}
              currentId={currentId}
              onPick={go}
              actions={actions}
            />
          </div>
        ) : null}
      </Drawer>

      <ConfirmDialog
        open={deleting !== null}
        title="Удалить разговор?"
        confirmLabel="Удалить"
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      >
        Переписка пропадёт, неподтверждённые правки из неё будут отброшены. Принятые правки
        останутся в резюме.
      </ConfirmDialog>
    </>
  )
}

/** Vacancy threads apart from the rest: a vacancy's thread is found by the
 *  vacancy, and mixed into one list by date the general ones got lost
 *  between them. */
function ThreadGroups({
  threads,
  currentId,
  onPick,
  actions,
}: {
  threads: ChatThread[]
  currentId: string
  onPick: (id: string) => void
  actions: ThreadActions
}) {
  const groups = [
    { title: 'По вакансиям', items: threads.filter((t) => t.vacancy) },
    { title: 'Общие', items: threads.filter((t) => !t.vacancy) },
  ].filter((g) => g.items.length)

  return (
    <>
      <button
        className="btn btn--small threads__new"
        onClick={() => onPick(NEW)}
        disabled={currentId === NEW}
      >
        <PlusIcon />
        Новый разговор
      </button>

      {/* The menu is fixed to the viewport; a scroll would leave it behind. */}
      <div className="threads__scroll" onScroll={() => actions.setMenuFor(null)}>
        {groups.map((group) => (
          <section key={group.title} className="threads__group">
            <h2 className="threads__heading">{group.title}</h2>
            <ul className="threads__list">
              {group.items.map((t) => (
                <li key={t.id}>
                  <ThreadItem
                    thread={t}
                    current={t.id === currentId}
                    onPick={onPick}
                    actions={actions}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </>
  )
}

function ThreadItem({
  thread,
  current,
  onPick,
  actions,
}: {
  thread: ChatThread
  current: boolean
  onPick: (id: string) => void
  actions: ThreadActions
}) {
  // A vacancy thread is named "Должность — Компания" by default; the company
  // goes to its own line, where a long title no longer cuts it off.
  const company = thread.vacancy?.company
  const title = shortTitleOf(thread)
  const menuOpen = actions.menuFor === thread.id
  const renaming = actions.renaming?.id === thread.id ? actions.renaming : null

  if (renaming) {
    return (
      <div className="threads__row">
        <input
          className="threads__rename"
          aria-label="Название разговора"
          autoFocus
          maxLength={120}
          value={renaming.text}
          onFocus={(e) => e.target.select()}
          onChange={(e) => actions.setRenaming({ id: thread.id, text: e.target.value })}
          onBlur={actions.rename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') actions.rename()
            if (e.key === 'Escape') {
              // Escape inside the phone's window would close the window too.
              e.stopPropagation()
              e.preventDefault()
              actions.setRenaming(null)
            }
          }}
        />
      </div>
    )
  }

  return (
    <div className={menuOpen ? 'threads__row is-open' : 'threads__row'}>
      <button
        className={current ? 'threads__item is-current' : 'threads__item'}
        aria-current={current ? 'page' : undefined}
        onClick={() => onPick(thread.id)}
        title={titleOf(thread)}
      >
        <span className="threads__text">
          <span className="threads__title">{title}</span>
          {company ? <span className="threads__sub">{company}</span> : null}
        </span>
        {thread.pending ? (
          <span className="threads__badge" title="Правки ждут решения">
            {thread.pending}
          </span>
        ) : null}
      </button>
      <ThreadMenu
        label={titleOf(thread)}
        open={menuOpen}
        onToggle={(open) => actions.setMenuFor(open ? thread.id : null)}
        onRename={() => actions.setRenaming({ id: thread.id, text: thread.title })}
        onDelete={() => actions.askDelete(thread)}
      />
    </div>
  )
}

/** The row's «⋯», shown on hover, with the menu under it. The menu is fixed
 *  to the viewport rather than placed in the row: the list scrolls and
 *  clips, and the last row's menu would be cut off at its bottom edge. */
function ThreadMenu({
  label,
  open,
  onToggle,
  onRename,
  onDelete,
}: {
  label: string
  open: boolean
  onToggle: (open: boolean) => void
  onRename: () => void
  onDelete: () => void
}) {
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const [at, setAt] = useState<{ top: number; left: number } | null>(null)

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      const target = event.target as Node
      if (!menu.current?.contains(target) && !trigger.current?.contains(target))
        onToggle(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open, onToggle])

  // Apart from the listener: onToggle is new on every render, and focus
  // must move once, when the menu opens, not on each revalidation.
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
  }, [open])

  function toggle() {
    const rect = trigger.current?.getBoundingClientRect()
    if (rect) setAt({ top: rect.bottom + 4, left: rect.right })
    onToggle(!open)
  }

  function choose(action: () => void) {
    onToggle(false)
    action()
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="threads__more"
        aria-label={`Действия с разговором «${label}»`}
        title="Действия"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
      >
        <MoreIcon />
      </button>
      {open ? (
        <div
          ref={menu}
          className="threads__menu"
          role="menu"
          style={at ? { top: at.top, left: at.left } : undefined}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              e.preventDefault()
              const items = [
                ...(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
              ]
              const at = items.indexOf(document.activeElement as HTMLElement)
              const step = e.key === 'ArrowDown' ? 1 : -1
              items[(at + step + items.length) % items.length]?.focus()
            }
            if (e.key === 'Escape') {
              e.stopPropagation()
              e.preventDefault()
              onToggle(false)
              trigger.current?.focus()
            }
          }}
        >
          <button
            role="menuitem"
            className="threads__menuitem"
            onClick={() => choose(onRename)}
          >
            <PencilIcon />
            Переименовать
          </button>
          <button
            role="menuitem"
            className="threads__menuitem threads__menuitem--danger"
            onClick={() => choose(onDelete)}
          >
            <TrashIcon />
            Удалить
          </button>
        </div>
      ) : null}
    </>
  )
}

function titleOf(thread: ChatThread) {
  return thread.title || 'Без названия'
}

/** A vacancy thread is named "Должность — Компания" by default. Where the
 *  company is shown on a line of its own, the title drops it rather than
 *  saying it twice; a title the user renamed is left as written. */
function shortTitleOf(thread: ChatThread) {
  const company = thread.vacancy?.company
  const suffix = company ? ` — ${company}` : ''
  return suffix && thread.title.endsWith(suffix)
    ? thread.title.slice(0, -suffix.length)
    : titleOf(thread)
}

function Conversation({
  thread,
  waiting,
}: {
  thread: ChatThread | null
  /** The thread list is still loading: an existing thread looks like a new
   *  one until it comes, and its history flashed the empty greeting. */
  waiting: boolean
}) {
  const navigate = useNavigate()
  const { data: state } = useAppState()
  const { data: messages, isLoading, error } = useChat(thread?.id ?? null)
  const { data: proposals } = useProposals()
  const { mutate } = useSWRConfig()
  const toast = useToast()

  const [text, setText] = useState('')
  // The message on its way, shown as a bubble until the history comes back
  // with it. Plain state, not a transition: a transition defers its updates,
  // so the box stayed full and nothing moved until the answer arrived — the
  // "Думаю…" line was there but the screen looked frozen.
  const [sending, setSending] = useState<{ text: string; vacancy?: VacancyRef } | null>(
    null,
  )
  const isSending = sending !== null
  const endRef = useRef<HTMLDivElement>(null)
  // Drawn once per thread opened (the component is keyed by it): a greeting
  // that changed on every render would flicker as messages load. The list
  // is chosen on render — the thread can arrive after the first one.
  const [draw] = useState(Math.random)
  const greetings = thread?.vacancy ? VACANCY_GREETINGS : GREETINGS
  const greeting = greetings[Math.floor(draw * greetings.length)]

  // Follow the conversation as it grows, the way a chat is expected to behave.
  // Guarded: scrollIntoView is missing in jsdom and in older mobile browsers,
  // and an absent nicety must not take the whole screen down with it.
  // The first jump is instant: a smooth one, started while the history was
  // still laying out, stopped short with the last message under the box.
  const followed = useRef(false)
  const loaded = messages !== undefined
  useEffect(() => {
    endRef.current?.scrollIntoView?.({
      behavior: followed.current ? 'smooth' : 'auto',
      block: 'end',
    })
    if (loaded) followed.current = true
  }, [loaded, messages?.length, proposals?.length, sending])

  /** Sends the box, or `answer` when a choice was pressed; `vacancy` goes
   *  with it when a vacancy card was picked. */
  async function send(answer?: string, vacancy?: VacancyRef) {
    const value = (answer ?? text).trim()
    if (!value || isSending) return
    // Cleared before the request, not after: leaving the text in the box
    // while the answer is on its way invites a second send of the same
    // message, and each one costs money.
    setText('')
    setSending({ text: value, vacancy })
    try {
      // A draft becomes a thread with its first message.
      const id = thread?.id ?? (await createThread()).id
      await sendMessage(id, value, vacancy?.id)
      await Promise.all([mutate(threadKey(id)), mutate(THREADS_KEY), mutate(PROPOSALS_KEY)])
      if (!thread) navigate(`/chat/${id}`, { replace: true })
    } catch (e) {
      setText(value)
      toast.error(e)
    } finally {
      setSending(null)
    }
  }

  async function accept(id: string) {
    try {
      await acceptProposal(id)
      // The profile changed, so the resume page and the scan filters have to
      // be re-read as well.
      await Promise.all([mutate(PROPOSALS_KEY), mutate(THREADS_KEY), mutate(STATE_KEY)])
      toast.info('Добавлено в резюме')
    } catch (e) {
      toast.error(e)
    }
  }

  async function reject(id: string) {
    try {
      await rejectProposal(id)
      await Promise.all([mutate(PROPOSALS_KEY), mutate(THREADS_KEY)])
    } catch (e) {
      toast.error(e)
    }
  }

  // Only this thread's: a change proposed in another conversation is
  // answered there, where its reason can be read.
  const pending = (proposals ?? []).filter((p) => thread && p.thread_id === thread.id)
  const hasMessages = (messages?.length ?? 0) > 0
  // Buttons only under the question still open: an older one was answered,
  // and pressing its button now would answer a question nobody is asking.
  const last = messages?.at(-1)
  const open = last?.role === 'assistant' && !isSending
  const openChoices = open ? (last.choices ?? []) : []

  return (
    <div className="chatmain">
      <ChatHeader thread={thread} />

      <ErrorNote error={error} />

      {state && !state.has_resume ? (
        <p className="chat__warn">
          Пока резюме не загружено, помощник не сможет ничего в него дописать — загрузите
          его на странице «Моё резюме».
        </p>
      ) : null}

      <div className="chat">
        {isLoading || waiting ? <Skeleton rows={3} /> : null}

        {!isLoading && !waiting && !hasMessages && !isSending ? (
          <div className="chat__empty">
            <p className="chat__greeting">{greeting}</p>
          </div>
        ) : null}

        {messages?.map((message, index) =>
          message.role === 'event' ? (
            // Not a turn of the conversation: the CV changed under it, and
            // what was agreed above may no longer be in the profile.
            <p key={`${message.created_at}-${index}`} className="chat__event">
              <span>{message.text}</span>
            </p>
          ) : (
            <div key={`${message.created_at}-${index}`} className="chat__turn">
              <div className={message.role === 'user' ? 'bubble bubble--me' : 'bubble'}>
                {message.role === 'assistant' ? (
                  <AssistantHead tools={message.tools} />
                ) : null}
                {/* The thread's own vacancy goes with every message; naming
                    it on each bubble would only repeat the title. */}
                {message.vacancy && message.vacancy.id !== thread?.vacancy?.id ? (
                  <p className="bubble__vacancy">
                    {vacancyLabel(message.vacancy.title, message.vacancy.company)}
                  </p>
                ) : null}
                {message.role === 'assistant' ? (
                  <div className="bubble__text">
                    <Reply text={message.text} />
                  </div>
                ) : (
                  <p className="bubble__text">{message.text}</p>
                )}
              </div>
              {message.vacancies?.length ? (
                <VacancyChoice
                  cards={message.vacancies}
                  onPick={
                    open && message === last ? (v) => send(`Разберём: ${v.title}`, v) : null
                  }
                />
              ) : null}
            </div>
          ),
        )}

        {openChoices.length ? (
          <div className="chat__choices">
            {openChoices.map((choice) => (
              <button key={choice} className="chat__example" onClick={() => send(choice)}>
                {choice}
              </button>
            ))}
            <p className="muted chat__choices-hint">или напишите свой ответ</p>
          </div>
        ) : null}

        {sending !== null ? (
          <>
            <div className="bubble bubble--me">
              {sending.vacancy ? (
                <p className="bubble__vacancy">
                  {vacancyLabel(sending.vacancy.title, sending.vacancy.company)}
                </p>
              ) : null}
              <p className="bubble__text">{sending.text}</p>
            </div>
            <p className="chat__thinking" role="status">
              <span className="typing" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              Думаю…
            </p>
          </>
        ) : null}

        {pending.length ? (
          <div className="stack chat__pending">
            <p className="eyebrow" style={{ margin: 0 }}>
              {pending.length === 1
                ? 'Одна правка ждёт вашего решения:'
                : `${pending.length} правки ждут вашего решения:`}
            </p>
            {pending.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                onAccept={accept}
                onReject={reject}
              />
            ))}
          </div>
        ) : null}

        <div ref={endRef} className="chat__end" />
      </div>

      <div className="chat__dock">
        <div className="chat__compose">
          <textarea
            className="chat__input"
            rows={2}
            value={text}
            placeholder={
              thread?.vacancy ? 'Что обсудить по этой вакансии?' : 'Напишите сообщение'
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, Shift+Enter breaks the line: the box is a chat
              // input, not a form field.
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
          />
          <button
            className="chat__send"
            onClick={() => send()}
            disabled={!text.trim() || isSending}
            aria-label="Отправить"
            title="Отправить"
          >
            <ArrowUpIcon />
          </button>
        </div>
      </div>
    </div>
  )
}

/** The bar over the conversation. It stays when the history scrolls: in a
 *  long thread the big page title was gone after the first screen, and with
 *  it which vacancy the talk was about. */
function ChatHeader({ thread }: { thread: ChatThread | null }) {
  const { data: state } = useAppState()
  const vacancy = thread?.vacancy
  const row = vacancy ? state?.found.find((r) => r.id === vacancy.id) : undefined
  const status = row?.status ? state?.statuses[row.status]?.title : undefined
  return (
    <header className={vacancy ? 'chathead' : 'chathead chathead--bare'}>
      <div className="chathead__inner">
        <div className="chathead__text">
          <h1 className="chathead__title">
            {thread ? shortTitleOf(thread) : 'Новый разговор'}
          </h1>
          {vacancy ? (
            <p className="chathead__meta">
              {vacancy.company ? <span>{vacancy.company}</span> : null}
              {status ? <span className="chathead__status">{status}</span> : null}
            </p>
          ) : null}
        </div>
        {row?.url ? (
          <a
            className="btn btn--quiet btn--small chathead__link"
            href={row.url}
            target="_blank"
            rel="noreferrer noopener"
          >
            Открыть вакансию <ExternalIcon />
          </a>
        ) : null}
      </div>
    </header>
  )
}

/** The assistant's turn: a mark that says who speaks, and the steps it took. */
function AssistantHead({ tools }: { tools: string[] }) {
  return (
    <div className="bubble__head">
      <span className="bubble__who">
        <BrandMark />
        Помощник
      </span>
      {/* What the assistant consulted before answering. A chat that silently
          did four things cannot be checked, so the steps stay visible. */}
      {tools.map((tool, index) => (
        <span key={`${tool}-${index}`} className="bubble__step">
          {TOOL_TITLES[tool] ?? tool}
        </span>
      ))}
    </div>
  )
}

type Block = { kind: 'text' | 'quote'; text: string }

/** Paragraphs of a reply, with «…» set apart.
 *
 * A paragraph that is all one «…» is text written for someone else — a reply
 * to an employer, a question to ask at the interview. It leaves the cabinet
 * by copy and paste, so it is a block with its own copy button. A quote that
 * spans paragraphs (a letter with a greeting and a signature) is one block
 * from the opening « to the closing ».
 *
 * The prompt asks for «…», but a model also fences such text with --- lines,
 * often glued to the first and last paragraph by a single newline. That is
 * the same block, so it is read as one too. */
function splitReply(text: string): Block[] {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
  const opensFence = /^-{3,}[ \t]*(\n|$)/
  const closesFence = /(^|\n)[ \t]*-{3,}$/
  const blocks: Block[] = []
  for (let i = 0; i < paragraphs.length; i++) {
    const p = paragraphs[i]
    if (opensFence.test(p)) {
      const first = p.replace(opensFence, '')
      const rest = [first, ...paragraphs.slice(i + 1)]
      // The closing fence is the next one after the opening, possibly in the
      // same paragraph; an empty remainder means "--- " stood alone.
      const end = rest.findIndex((q, j) => (j > 0 || q !== '') && closesFence.test(q))
      if (end >= 0) {
        const quote = rest
          .slice(0, end + 1)
          .map((q, j) => (j === end ? q.replace(closesFence, '') : q).trim())
          .filter(Boolean)
          .join('\n\n')
        if (quote) {
          blocks.push({ kind: 'quote', text: quote })
          i += end
          continue
        }
      }
    }
    if (p.startsWith('«')) {
      const end = paragraphs.findIndex((q, j) => j >= i && /»[.!?…]?$/.test(q))
      if (end >= 0) {
        const quote = paragraphs.slice(i, end + 1).join('\n\n')
        blocks.push({
          kind: 'quote',
          text: quote.replace(/^«/, '').replace(/»([.!?…]?)$/, '$1'),
        })
        i = end
        continue
      }
    }
    blocks.push({ kind: 'text', text: p })
  }
  return blocks
}

function Reply({ text }: { text: string }) {
  return (
    <>
      {splitReply(text).map((block, index) =>
        block.kind === 'quote' ? (
          <Quote key={index} text={block.text} />
        ) : (
          <p key={index} className="bubble__p">
            <Inline text={block.text} />
          </p>
        ),
      )}
    </>
  )
}

function Quote({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  async function copy() {
    let ok: boolean
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      // Same fallback as the cover letter (report.tsx): a silent failure
      // would leave an older text in the clipboard to be sent instead.
      ok = copySelection(ref.current)
    }
    setState(ok ? 'copied' : 'failed')
    window.setTimeout(() => setState('idle'), 2500)
  }

  return (
    <figure className="quote">
      <div className="quote__text" ref={ref}>
        <Inline text={text} />
      </div>
      <button type="button" className="quote__copy" onClick={copy}>
        <CopyIcon />
        {state === 'copied'
          ? 'Скопировано'
          : state === 'failed'
            ? 'Не скопировалось — Ctrl+C'
            : 'Скопировать'}
      </button>
    </figure>
  )
}

/** Vacancies the assistant offered to pick, as the database has them.
 *  `onPick` is null under a question already answered: the cards stay as a
 *  record, without a button that would answer it again. */
function VacancyChoice({
  cards,
  onPick,
}: {
  cards: VacancyCard[]
  onPick: ((card: VacancyCard) => void) | null
}) {
  return (
    <ul className="vchoice">
      {cards.map((card) => (
        <li key={card.id} className="vchoice__card">
          <div className="vchoice__head">
            <span className="vchoice__title">{vacancyLabel(card.title, card.company)}</span>
            {card.score != null ? (
              <span className="vchoice__score">{card.score.toFixed(1)}</span>
            ) : null}
          </div>
          <p className="vchoice__meta">
            {[
              card.salary_text,
              card.remote ? 'удалённо' : card.location,
              card.saved ? 'в избранном' : null,
            ]
              .filter(Boolean)
              .join(' · ') || ' '}
          </p>
          <div className="vchoice__actions">
            {onPick ? (
              <button className="btn btn--small" onClick={() => onPick(card)}>
                Разобрать эту
              </button>
            ) : null}
            {card.url ? (
              <a
                className="btn btn--quiet btn--small"
                href={card.url}
                target="_blank"
                rel="noreferrer noopener"
              >
                Открыть <ExternalIcon />
              </a>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  )
}

function vacancyLabel(title: string, company: string | null | undefined) {
  return company ? `${title} — ${company}` : title
}

/** `**bold**` from the model, and nothing else.
 *
 * The model writes light Markdown whatever the prompt says, and raw asterisks
 * read as noise. A full Markdown renderer would be a dependency for one
 * construct, and would also render links and HTML the model was never meant
 * to produce — so only bold is honoured. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/\*\*(.+?)\*\*/g)
  return <>{parts.map((part, index) => (index % 2 ? <b key={index}>{part}</b> : part))}</>
}
