/** Экран помощника: что видит пользователь и чего он не видит.
 *
 * Главное здесь — правки показываются карточками с двумя кнопками, а не
 * применяются сами. Уход со страницы ничего не решает: молчание — это отказ.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ChatMessage, ChatProposal } from '../api/types'

const mocks = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  proposals: [] as ChatProposal[],
  hasResume: true,
}))

vi.mock('../api/hooks', () => ({
  CHAT_KEY: '/api/chat',
  PROPOSALS_KEY: '/api/chat/proposals',
  STATE_KEY: '/api/state',
  useChat: () => ({ data: mocks.messages, isLoading: false, error: null }),
  useProposals: () => ({ data: mocks.proposals }),
  useAppState: () => ({ data: { has_resume: mocks.hasResume } }),
  sendMessage: vi.fn(),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
  clearChat: vi.fn(),
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: vi.fn() }) }))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn() }),
}))

const { ChatPage } = await import('./ChatPage')

function message(
  role: 'user' | 'assistant',
  text: string,
  tools: string[] = [],
): ChatMessage {
  return { role, text, tools, created_at: `2026-09-22T12:0${text.length % 10}:00Z` }
}

const proposal: ChatProposal = {
  id: 'p1',
  kind: 'profile_patch',
  status: 'pending',
  arguments: { op: 'add_skill', value: 'Kubernetes' },
  current: null,
  proposed: null,
  rationale: 'Вы сказали, что работали с кластерами',
  created_at: '2026-09-22T12:00:00Z',
}

describe('ChatPage', () => {
  it('пустой разговор объясняет, что делать, и не выглядит поломкой', () => {
    mocks.messages = []
    mocks.proposals = []
    render(<ChatPage />)

    expect(screen.getByText(/вы подтверждаете сами/)).toBeTruthy()
  })

  it('сообщения показаны в том порядке, в котором пришли', () => {
    mocks.messages = [
      message('user', 'знаю kubernetes'),
      message('assistant', 'Подготовил правку.'),
    ]
    mocks.proposals = []
    const { container } = render(<ChatPage />)

    const bubbles = [...container.querySelectorAll('.bubble__text')].map(
      (n) => n.textContent,
    )
    expect(bubbles).toEqual(['знаю kubernetes', 'Подготовил правку.'])
  })

  it('смена резюме видна в ленте отметкой, а не репликой', () => {
    mocks.messages = [
      message('user', 'добавь Claude'),
      {
        ...message('assistant', 'x'),
        role: 'event',
        text: 'Загружено новое резюме «cv.md».',
      },
    ]
    mocks.proposals = []
    const { container } = render(<ChatPage />)

    expect(container.querySelector('.chat__event')?.textContent).toMatch(/новое резюме/)
    expect(container.querySelectorAll('.bubble')).toHaveLength(1)
  })

  it('видно, что помощник смотрел, — молча сделанное проверить нельзя', () => {
    mocks.messages = [message('assistant', 'В профиле нет Kubernetes.', ['get_profile'])]
    mocks.proposals = []
    render(<ChatPage />)

    expect(screen.getByText('посмотрел резюме')).toBeTruthy()
  })

  it('ждущая правка показана карточкой с двумя кнопками', () => {
    mocks.messages = [message('assistant', 'Подтвердите.')]
    mocks.proposals = [proposal]
    render(<ChatPage />)

    expect(screen.getByText('Kubernetes')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Добавить' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Не надо' })).toBeTruthy()
  })

  it('вопрос помощника показан с вариантами, и нажатый вариант уходит ответом', async () => {
    const { sendMessage } = await import('../api/hooks')
    mocks.messages = [
      message('user', 'добавь Claude Code'),
      {
        ...message('assistant', 'Как вы использовали Claude Code?'),
        choices: ['Основной способ писать код', 'Помощник на части задач'],
      },
    ]
    mocks.proposals = []
    render(<ChatPage />)
    expect(screen.getByText(/напишите свой ответ/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Основной способ писать код' }))
    expect(sendMessage).toHaveBeenCalledWith('Основной способ писать код')
    // Hidden while the answer is on its way: a second press is a second paid reply.
    expect(screen.queryByRole('button', { name: 'Помощник на части задач' })).toBeNull()
  })

  it('варианты уже отвеченного вопроса не показываются', () => {
    mocks.messages = [
      { ...message('assistant', 'Как?'), choices: ['так', 'иначе'] },
      message('user', 'так'),
      message('assistant', 'Подготовил правку.'),
    ]
    mocks.proposals = []
    render(<ChatPage />)

    expect(screen.queryByRole('button', { name: 'иначе' })).toBeNull()
  })

  it('без резюме сказано, почему помощник не сможет ничего дописать', () => {
    mocks.messages = []
    mocks.proposals = []
    mocks.hasResume = false
    render(<ChatPage />)

    expect(screen.getByText(/загрузите его на странице/i)).toBeTruthy()
    mocks.hasResume = true
  })
})
