/** "Помощник" — the chat.
 *
 * One column: the conversation, the pending changes, the input. The changes
 * sit under the last message rather than in a side panel, because they are
 * the answer to what was just said and the user should not have to look
 * elsewhere to find them.
 *
 * Nothing here writes to the profile. Every change goes through a card with
 * two buttons, and closing the page decides nothing — which is the product
 * rule this screen exists to make visible.
 */

import { useEffect, useRef, useState, useTransition } from 'react'
import { useSWRConfig } from 'swr'
import {
  CHAT_KEY,
  PROPOSALS_KEY,
  STATE_KEY,
  acceptProposal,
  clearChat,
  rejectProposal,
  sendMessage,
  useAppState,
  useChat,
  useProposals,
} from '../api/hooks'
import { ProposalCard } from '../components/ProposalCard'
import { useToast } from '../components/toast'
import { Card, ErrorNote, Skeleton } from '../components/ui'
import '../components/chat.css'

/** What each tool is called when the chat says what it looked at. The names
 *  are internal; "посмотрел резюме" is what actually happened. */
const TOOL_TITLES: Record<string, string> = {
  get_profile: 'посмотрел резюме',
  get_vacancy: 'открыл вакансию',
  list_vacancies: 'просмотрел список вакансий',
  skill_gap: 'разобрал требования',
  open_gaps: 'сверил с купленными отчётами',
  get_spend: 'проверил расходы',
  propose_profile_patch: 'подготовил правку резюме',
  set_vacancy_status: 'подготовил смену статуса',
  save_vacancy: 'подготовил избранное',
}

const EXAMPLES = [
  'Забыл написать — я год настраивал Kubernetes в последней компании.',
  'Что мне не хватает по вакансии, которую я сохранил?',
  'Почему ты отсеял вакансии про аналитику?',
]

export function ChatPage() {
  const { data: state } = useAppState()
  const { data: messages, isLoading, error } = useChat()
  const { data: proposals } = useProposals()
  const { mutate } = useSWRConfig()
  const toast = useToast()

  const [text, setText] = useState('')
  const [isSending, startSending] = useTransition()
  const endRef = useRef<HTMLDivElement>(null)

  // Follow the conversation as it grows, the way a chat is expected to behave.
  // Guarded: scrollIntoView is missing in jsdom and in older mobile browsers,
  // and an absent nicety must not take the whole screen down with it.
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [messages?.length, proposals?.length])

  /** Sends the box, or `answer` when a choice button was pressed. */
  function send(answer?: string) {
    const value = (answer ?? text).trim()
    if (!value || isSending) return
    startSending(async () => {
      try {
        // Cleared before the request, not after: leaving the text in the box
        // while the answer is on its way invites a second send of the same
        // message, and each one costs money.
        setText('')
        await sendMessage(value)
        await Promise.all([mutate(CHAT_KEY), mutate(PROPOSALS_KEY)])
      } catch (e) {
        setText(value)
        toast.error(e)
      }
    })
  }

  async function accept(id: string) {
    try {
      await acceptProposal(id)
      // The profile changed, so the resume page and the scan filters have to
      // be re-read as well.
      await Promise.all([mutate(PROPOSALS_KEY), mutate(STATE_KEY)])
      toast.info('Добавлено в резюме')
    } catch (e) {
      toast.error(e)
    }
  }

  async function reject(id: string) {
    try {
      await rejectProposal(id)
      await mutate(PROPOSALS_KEY)
    } catch (e) {
      toast.error(e)
    }
  }

  async function clear() {
    try {
      const out = await clearChat()
      await Promise.all([mutate(CHAT_KEY), mutate(PROPOSALS_KEY)])
      toast.info(
        out.proposals
          ? `Разговор очищен, ${out.proposals} неподтверждённых правок отброшено`
          : 'Разговор очищен',
      )
    } catch (e) {
      toast.error(e)
    }
  }

  const pending = proposals ?? []
  const hasMessages = (messages?.length ?? 0) > 0
  // Buttons only under the question still open: an older one was answered,
  // and pressing its button now would answer a question nobody is asking.
  const last = messages?.at(-1)
  const openChoices = last?.role === 'assistant' ? (last.choices ?? []) : []

  return (
    <Card
      title="Помощник"
      actions={
        hasMessages ? (
          <button className="btn btn--ghost btn--small" onClick={clear}>
            Очистить
          </button>
        ) : undefined
      }
    >
      <ErrorNote error={error} />

      {state && !state.has_resume ? (
        <p className="muted" style={{ marginTop: 0 }}>
          Пока резюме не загружено, помощник не сможет ничего в него дописать — загрузите
          его на странице «Моё резюме».
        </p>
      ) : null}

      <div className="chat">
        {isLoading ? <Skeleton rows={3} /> : null}

        {!isLoading && !hasMessages ? (
          <div className="chat__empty">
            <p className="muted" style={{ marginTop: 0 }}>
              Расскажите то, чего нет в резюме, или спросите про найденные вакансии. Всё,
              что помощник предложит записать, вы подтверждаете сами.
            </p>
            <div className="stack">
              {EXAMPLES.map((example) => (
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
            <div
              key={`${message.created_at}-${index}`}
              className={message.role === 'user' ? 'bubble bubble--me' : 'bubble'}
            >
              {message.tools.length ? (
                <p className="bubble__tools">
                  {message.tools.map((t) => TOOL_TITLES[t] ?? t).join(', ')}
                </p>
              ) : null}
              <p className="bubble__text">{message.text}</p>
            </div>
          ),
        )}

        {openChoices.length && !isSending ? (
          <div className="chat__choices">
            {openChoices.map((choice) => (
              <button key={choice} className="chat__example" onClick={() => send(choice)}>
                {choice}
              </button>
            ))}
            <p className="muted chat__choices-hint">или напишите свой ответ</p>
          </div>
        ) : null}

        {isSending ? <p className="muted chat__thinking">Думаю…</p> : null}

        {pending.length ? (
          <div className="stack">
            <p className="muted" style={{ margin: 0 }}>
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

        <div ref={endRef} />
      </div>

      <div className="chat__compose">
        <textarea
          className="chat__input"
          rows={2}
          value={text}
          placeholder="Напишите сообщение"
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
        <button className="btn" onClick={() => send()} disabled={!text.trim() || isSending}>
          {isSending ? '…' : 'Отправить'}
        </button>
      </div>
    </Card>
  )
}
