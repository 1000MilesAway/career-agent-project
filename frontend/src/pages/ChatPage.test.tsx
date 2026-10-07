/** Экран помощника: что видит пользователь и чего он не видит.
 *
 * Главное здесь — правки показываются карточками с двумя кнопками, а не
 * применяются сами. Уход со страницы ничего не решает: молчание — это отказ.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ChatMessage, ChatProposal } from '../api/types'

const mocks = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  proposals: [] as ChatProposal[],
  hasResume: true,
  found: [{ id: 'v1', title: 'Python разработчик', company: 'Ромашка' }],
}))

vi.mock('../api/hooks', () => ({
  CHAT_KEY: '/api/chat',
  PROPOSALS_KEY: '/api/chat/proposals',
  STATE_KEY: '/api/state',
  useChat: () => ({ data: mocks.messages, isLoading: false, error: null }),
  useProposals: () => ({ data: mocks.proposals }),
  useAppState: () => ({ data: { has_resume: mocks.hasResume, found: mocks.found } }),
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

/** The page reads `?vacancy=` off the URL, so it needs a router. */
function renderPage(path = '/chat') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ChatPage />
    </MemoryRouter>,
  )
}

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
  it('пустой разговор предлагает, с чего начать, и не выглядит поломкой', () => {
    mocks.messages = []
    mocks.proposals = []
    renderPage()

    expect(screen.getByText(/Можно начать/)).toBeTruthy()
  })

  it('сообщения показаны в том порядке, в котором пришли', () => {
    mocks.messages = [
      message('user', 'знаю kubernetes'),
      message('assistant', 'Подготовил правку.'),
    ]
    mocks.proposals = []
    const { container } = renderPage()

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
    const { container } = renderPage()

    expect(container.querySelector('.chat__event')?.textContent).toMatch(/новое резюме/)
    expect(container.querySelectorAll('.bubble')).toHaveLength(1)
  })

  it('видно, что помощник смотрел, — молча сделанное проверить нельзя', () => {
    mocks.messages = [message('assistant', 'В профиле нет Kubernetes.', ['get_profile'])]
    mocks.proposals = []
    renderPage()

    expect(screen.getByText('посмотрел резюме')).toBeTruthy()
  })

  it('ждущая правка показана карточкой с двумя кнопками', () => {
    mocks.messages = [message('assistant', 'Подтвердите.')]
    mocks.proposals = [proposal]
    renderPage()

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
    renderPage()
    expect(screen.getByText(/напишите свой ответ/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Основной способ писать код' }))
    expect(sendMessage).toHaveBeenCalledWith('Основной способ писать код', undefined)
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
    renderPage()

    expect(screen.queryByRole('button', { name: 'иначе' })).toBeNull()
  })

  it('без резюме сказано, почему помощник не сможет ничего дописать', () => {
    mocks.messages = []
    mocks.proposals = []
    mocks.hasResume = false
    renderPage()

    expect(screen.getByText(/загрузите его на странице/i)).toBeTruthy()
    mocks.hasResume = true
  })

  it('отправленное сообщение и «Думаю…» видны сразу, до ответа', async () => {
    const hooks = await import('../api/hooks')
    let answer: () => void = () => {}
    vi.mocked(hooks.sendMessage).mockImplementationOnce(
      () => new Promise((resolve) => (answer = () => resolve({} as never))),
    )
    mocks.messages = []
    mocks.proposals = []
    const { container } = renderPage()

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'что ответить?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    expect(screen.getByRole('status').textContent).toMatch(/Думаю/)
    expect(container.querySelector('.bubble--me')?.textContent).toBe('что ответить?')
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('')

    await act(async () => answer())
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('«Обсудить» из избранного прикрепляет вакансию к сообщению', async () => {
    const { sendMessage } = await import('../api/hooks')
    vi.mocked(sendMessage).mockClear()
    mocks.messages = []
    mocks.proposals = []
    renderPage('/chat?vacancy=v1')

    expect(screen.getByText('Python разработчик — Ромашка')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'что ответить?' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    })
    expect(sendMessage).toHaveBeenCalledWith('что ответить?', 'v1')
  })

  it('прикрепление можно снять, и сообщение уйдёт без вакансии', async () => {
    const { sendMessage } = await import('../api/hooks')
    vi.mocked(sendMessage).mockClear()
    mocks.messages = []
    mocks.proposals = []
    renderPage('/chat?vacancy=v1')

    fireEvent.click(screen.getByRole('button', { name: 'Не прикреплять вакансию' }))
    expect(screen.queryByText('Python разработчик — Ромашка')).toBeNull()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'привет' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    })
    expect(sendMessage).toHaveBeenCalledWith('привет', undefined)
  })

  it('у сообщения о вакансии видно, о какой', () => {
    mocks.messages = [
      {
        ...message('user', 'что ответить?'),
        vacancy: { id: 'v1', title: 'Python разработчик', company: 'Ромашка' },
      },
    ]
    mocks.proposals = []
    const { container } = renderPage()

    expect(container.querySelector('.bubble__vacancy')?.textContent).toBe(
      'Python разработчик — Ромашка',
    )
  })
})
