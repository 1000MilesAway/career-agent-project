/** "Помощник" — the chat.
 *
 * Several conversations, listed on the left (a select on a phone): one
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
import { ProposalCard } from '../components/ProposalCard'
import { useToast } from '../components/toast'
import { ExternalIcon } from '../components/icons'
import { ErrorNote, PageHead, Skeleton } from '../components/ui'
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

const EXAMPLES = [
  'Забыл написать — я год настраивал Kubernetes в последней компании.',
  'Что мне не хватает по вакансии, которую я сохранил?',
  'Почему ты отсеял вакансии про аналитику?',
  'Мне ответили из Ромашки, вот их сообщение: «…». Что ответить?',
]

/** A vacancy's thread starts from what people come to it for. */
const VACANCY_EXAMPLES = [
  'Чего мне не хватает для этой вакансии?',
  'Мне ответили, вот их сообщение: «…». Что ответить?',
  'К чему готовиться на интервью?',
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
      <Conversation key={threadId} thread={current ?? null} />
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

function ThreadList({ threads, currentId }: { threads: ChatThread[]; currentId: string }) {
  const navigate = useNavigate()
  return (
    <nav className="threads" aria-label="Разговоры">
      <button
        className="btn btn--small threads__new"
        onClick={() => navigate(`/chat/${NEW}`)}
        disabled={currentId === NEW}
      >
        Новый разговор
      </button>

      <ul className="threads__list">
        {threads.map((t) => (
          <li key={t.id}>
            <button
              className={t.id === currentId ? 'threads__item is-current' : 'threads__item'}
              aria-current={t.id === currentId ? 'page' : undefined}
              onClick={() => navigate(`/chat/${t.id}`)}
              title={titleOf(t)}
            >
              <span className="threads__title">{titleOf(t)}</span>
              {t.pending ? (
                <span className="threads__badge" title="Правки ждут решения">
                  {t.pending}
                </span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>

      {/* A phone has no room for a column; the same list as a select. */}
      <select
        className="threads__select"
        aria-label="Разговор"
        value={currentId}
        onChange={(e) => navigate(`/chat/${e.target.value}`)}
      >
        <option value={NEW}>Новый разговор</option>
        {threads.map((t) => (
          <option key={t.id} value={t.id}>
            {t.pending ? `${titleOf(t)} · ${t.pending}` : titleOf(t)}
          </option>
        ))}
      </select>
    </nav>
  )
}

function titleOf(thread: ChatThread) {
  return thread.title || 'Без названия'
}

function Conversation({ thread }: { thread: ChatThread | null }) {
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
  const [renaming, setRenaming] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

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

  async function rename() {
    const title = renaming?.trim()
    setRenaming(null)
    if (!thread || !title || title === thread.title) return
    try {
      await renameThread(thread.id, title)
      await mutate(THREADS_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  async function remove() {
    setConfirmDelete(false)
    if (!thread) return
    try {
      const out = await deleteThread(thread.id)
      await Promise.all([mutate(THREADS_KEY), mutate(PROPOSALS_KEY)])
      navigate('/chat', { replace: true })
      toast.info(
        out.proposals
          ? `Разговор удалён, ${out.proposals} неподтверждённых правок отброшено`
          : 'Разговор удалён',
      )
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
  const examples = thread?.vacancy ? VACANCY_EXAMPLES : EXAMPLES

  return (
    <div className="chatmain">
      <PageHead
        title={
          renaming !== null ? (
            <input
              className="chat__rename"
              aria-label="Название разговора"
              autoFocus
              maxLength={120}
              value={renaming}
              onChange={(e) => setRenaming(e.target.value)}
              onBlur={rename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') rename()
                if (e.key === 'Escape') setRenaming(null)
              }}
            />
          ) : thread ? (
            titleOf(thread)
          ) : (
            'Помощник'
          )
        }
        actions={
          thread ? (
            <>
              <button
                className="btn btn--quiet btn--small"
                onClick={() => setRenaming(thread.title)}
              >
                Переименовать
              </button>
              <button
                className="btn btn--quiet btn--small"
                onClick={() => setConfirmDelete(true)}
              >
                Удалить
              </button>
            </>
          ) : undefined
        }
      />

      <ErrorNote error={error} />

      {state && !state.has_resume ? (
        <p className="chat__warn">
          Пока резюме не загружено, помощник не сможет ничего в него дописать — загрузите
          его на странице «Моё резюме».
        </p>
      ) : null}

      <div className="chat">
        {isLoading ? <Skeleton rows={3} /> : null}

        {!isLoading && !hasMessages && !isSending ? (
          <div className="chat__empty">
            <p className="eyebrow">Можно начать, например, так</p>
            <div className="stack">
              {examples.map((example) => (
                <button
                  key={example}
                  className="chat__example"
                  onClick={() => setText(example)}
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {messages?.map((message, index) =>
          message.role === 'event' ? (
            // Not a turn of the conversation: the CV changed under it, and
            // what was agreed above may no longer be in the profile.
            <p key={`${message.created_at}-${index}`} className="chat__event">
              {message.text}
            </p>
          ) : (
            <div key={`${message.created_at}-${index}`} className="chat__turn">
              <div className={message.role === 'user' ? 'bubble bubble--me' : 'bubble'}>
                {/* The thread's own vacancy goes with every message; naming
                    it on each bubble would only repeat the title. */}
                {message.vacancy && message.vacancy.id !== thread?.vacancy?.id ? (
                  <p className="bubble__vacancy">
                    {vacancyLabel(message.vacancy.title, message.vacancy.company)}
                  </p>
                ) : null}
                {message.tools.length ? (
                  <p className="bubble__tools">
                    {message.tools.map((t) => TOOL_TITLES[t] ?? t).join(', ')}
                  </p>
                ) : null}
                <p className="bubble__text">
                  {message.role === 'assistant' ? (
                    <Inline text={message.text} />
                  ) : (
                    message.text
                  )}
                </p>
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
            className="btn chat__send"
            onClick={() => send()}
            disabled={!text.trim() || isSending}
          >
            {isSending ? '…' : 'Отправить'}
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Удалить разговор?"
        confirmLabel="Удалить"
        onConfirm={remove}
        onCancel={() => setConfirmDelete(false)}
      >
        Переписка пропадёт, неподтверждённые правки из неё будут отброшены. Принятые правки
        останутся в резюме.
      </ConfirmDialog>
    </div>
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
