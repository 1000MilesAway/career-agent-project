/** Экран помощника: что видит пользователь и чего он не видит.
 *
 * Главное здесь — правки показываются карточками с двумя кнопками, а не
 * применяются сами. Уход со страницы ничего не решает: молчание — это отказ.
 */

import { render, screen } from '@testing-library/react'
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

  it('без резюме сказано, почему помощник не сможет ничего дописать', () => {
    mocks.messages = []
    mocks.proposals = []
    mocks.hasResume = false
    render(<ChatPage />)

    expect(screen.getByText(/загрузите его на странице/i)).toBeTruthy()
    mocks.hasResume = true
  })
})
