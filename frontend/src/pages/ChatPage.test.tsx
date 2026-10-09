/** Экран помощника: что видит пользователь и чего он не видит.
 *
 * Главное здесь — правки показываются карточками с двумя кнопками, а не
 * применяются сами. Уход со страницы ничего не решает: молчание — это отказ.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ChatMessage, ChatProposal, ChatThread } from '../api/types'

const mocks = vi.hoisted(() => ({
  messages: [] as ChatMessage[],
  proposals: [] as ChatProposal[],
  hasResume: true,
  threads: [] as ChatThread[],
}))

vi.mock('../api/hooks', () => ({
  THREADS_KEY: '/api/chat/threads',
  PROPOSALS_KEY: '/api/chat/proposals',
  STATE_KEY: '/api/state',
  threadKey: (id: string) => `/api/chat/threads/${id}`,
  useThreads: () => ({ data: mocks.threads }),
  useChat: (id: string | null) => ({
    data: id ? mocks.messages : undefined,
    isLoading: false,
    error: null,
  }),
  useProposals: () => ({ data: mocks.proposals }),
  useAppState: () => ({ data: { has_resume: mocks.hasResume } }),
  sendMessage: vi.fn(),
  createThread: vi.fn(),
  renameThread: vi.fn(),
  deleteThread: vi.fn(),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: vi.fn() }) }))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn() }),
}))

const { ChatPage } = await import('./ChatPage')

const THREAD: ChatThread = {
  id: 't1',
  title: 'Про Kubernetes',
  vacancy: null,
  last_message_at: '2026-10-07T12:00:00Z',
  pending: 0,
}

const VACANCY_THREAD: ChatThread = {
  id: 't2',
  title: 'Python разработчик — Ромашка',
  vacancy: { id: 'v1', title: 'Python разработчик', company: 'Ромашка' },
  last_message_at: '2026-10-07T11:00:00Z',
  pending: 1,
}

function Where() {
  const location = useLocation()
  return <span data-testid="where">{location.pathname}</span>
}

/** The page reads the thread off the path, so it needs a router. */
function renderPage(path = '/chat/t1') {
  if (!mocks.threads.length) mocks.threads = [THREAD, VACANCY_THREAD]
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:threadId" element={<ChatPage />} />
      </Routes>
      <Where />
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
  thread_id: 't1',
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
    expect(sendMessage).toHaveBeenCalledWith('t1', 'Основной способ писать код', undefined)
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

  it('у сообщения о другой вакансии видно, о какой', () => {
    mocks.messages = [
      {
        ...message('user', 'что ответить?'),
        vacancy: { id: 'v9', title: 'Python разработчик', company: 'Ромашка' },
      },
    ]
    mocks.proposals = []
    const { container } = renderPage()

    expect(container.querySelector('.bubble__vacancy')?.textContent).toBe(
      'Python разработчик — Ромашка',
    )
  })

  it('в чате вакансии её название не повторяется на каждом сообщении', () => {
    mocks.messages = [
      {
        ...message('user', 'что ответить?'),
        vacancy: { id: 'v1', title: 'Python разработчик', company: 'Ромашка' },
      },
    ]
    mocks.proposals = []
    const { container } = renderPage('/chat/t2')

    expect(container.querySelector('.bubble__vacancy')).toBeNull()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(
      'Python разработчик — Ромашка',
    )
  })

  it('разговоры перечислены, ждущие правки отмечены числом', () => {
    mocks.messages = []
    mocks.proposals = []
    renderPage()

    const nav = screen.getByRole('navigation', { name: 'Разговоры' })
    expect(nav.textContent).toMatch(/Про Kubernetes/)
    expect(nav.querySelector('.threads__badge')?.textContent).toBe('1')
  })

  it('разговоры по вакансиям отдельно от общих, компания — второй строкой', () => {
    mocks.messages = []
    mocks.proposals = []
    renderPage()

    const nav = screen.getByRole('navigation', { name: 'Разговоры' })
    const groups = [...nav.querySelectorAll('.threads__group')].map((g) => ({
      heading: g.querySelector('h2')?.textContent,
      titles: [...g.querySelectorAll('.threads__title')].map((t) => t.textContent),
      subs: [...g.querySelectorAll('.threads__sub')].map((t) => t.textContent),
    }))
    expect(groups).toEqual([
      { heading: 'По вакансиям', titles: ['Python разработчик'], subs: ['Ромашка'] },
      { heading: 'Общие', titles: ['Про Kubernetes'], subs: [] },
    ])
  })

  it('/chat открывает последний разговор', () => {
    mocks.messages = []
    renderPage('/chat')
    expect(screen.getByTestId('where').textContent).toBe('/chat/t1')
  })

  it('правка из другого разговора здесь не показывается', () => {
    mocks.messages = [message('assistant', 'Подтвердите.')]
    mocks.proposals = [{ ...proposal, thread_id: 't2' }]
    renderPage()

    expect(screen.queryByText('Kubernetes')).toBeNull()
  })

  it('новый разговор создаётся первым сообщением', async () => {
    const hooks = await import('../api/hooks')
    vi.mocked(hooks.createThread).mockResolvedValueOnce({ ...THREAD, id: 't3' })
    vi.mocked(hooks.sendMessage).mockClear()
    vi.mocked(hooks.sendMessage).mockResolvedValueOnce({} as never)
    mocks.messages = []
    mocks.proposals = []
    mocks.threads = [THREAD, VACANCY_THREAD, { ...THREAD, id: 't3' }]
    renderPage('/chat/new')

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'привет' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    })
    expect(hooks.createThread).toHaveBeenCalledWith()
    expect(hooks.sendMessage).toHaveBeenCalledWith('t3', 'привет', undefined)
    expect(screen.getByTestId('where').textContent).toBe('/chat/t3')
    mocks.threads = []
  })

  it('«Обсудить» открывает чат этой вакансии', async () => {
    const hooks = await import('../api/hooks')
    vi.mocked(hooks.createThread).mockResolvedValueOnce(VACANCY_THREAD)
    mocks.messages = []
    await act(async () => {
      renderPage('/chat?vacancy=v1')
    })
    expect(hooks.createThread).toHaveBeenCalledWith('v1')
    expect(screen.getByTestId('where').textContent).toBe('/chat/t2')
  })

  it('вакансии на выбор — карточками, выбранная уходит вместе с сообщением', async () => {
    const { sendMessage } = await import('../api/hooks')
    vi.mocked(sendMessage).mockClear()
    const card = {
      id: 'v5',
      title: 'Backend Python',
      company: 'Сбер',
      salary_text: '250–300 тыс.',
      location: 'Москва',
      remote: true,
      score: 4.2,
      saved: true,
      url: 'https://example.com/v5',
    }
    mocks.messages = [
      message('user', 'что есть?'),
      { ...message('assistant', 'Какую разберём?'), vacancies: [card] },
    ]
    mocks.proposals = []
    renderPage()

    expect(screen.getByText('Backend Python — Сбер')).toBeTruthy()
    expect(screen.getByText('250–300 тыс. · удалённо · в избранном')).toBeTruthy()
    expect(screen.getByRole('link', { name: /Открыть/ }).getAttribute('href')).toBe(
      'https://example.com/v5',
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Разобрать эту' }))
    })
    expect(sendMessage).toHaveBeenCalledWith('t1', 'Разберём: Backend Python', 'v5')
  })

  it('под отвеченным выбором карточки остаются, а кнопки выбора нет', () => {
    mocks.messages = [
      {
        ...message('assistant', 'Какую?'),
        vacancies: [
          {
            id: 'v5',
            title: 'Backend Python',
            company: null,
            salary_text: null,
            location: null,
            remote: null,
            score: null,
            saved: false,
            url: null,
          },
        ],
      },
      message('user', 'Разберём: Backend Python'),
      message('assistant', 'Хорошо.'),
    ]
    mocks.proposals = []
    renderPage()

    expect(screen.getByText('Backend Python')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Разобрать эту' })).toBeNull()
  })

  describe('меню «⋯» у разговора в списке', () => {
    function openMenu(title: string) {
      fireEvent.click(
        screen.getByRole('button', { name: `Действия с разговором «${title}»` }),
      )
    }

    it('в шапке разговора кнопок больше нет — они в меню строки', () => {
      mocks.messages = []
      mocks.proposals = []
      renderPage()
      expect(screen.queryByRole('button', { name: 'Переименовать' })).toBeNull()

      openMenu('Про Kubernetes')
      expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual([
        'Переименовать',
        'Удалить',
      ])
    })

    it('переименование — прямо в строке, Enter сохраняет', async () => {
      mocks.messages = []
      mocks.proposals = []
      const hooks = await import('../api/hooks')
      vi.mocked(hooks.renameThread).mockClear()
      renderPage()

      openMenu('Про Kubernetes')
      fireEvent.click(screen.getByRole('menuitem', { name: 'Переименовать' }))
      const input = screen.getByRole('textbox', { name: 'Название разговора' })
      fireEvent.change(input, { target: { value: 'Кубер' } })
      await act(async () => {
        fireEvent.keyDown(input, { key: 'Enter' })
      })
      expect(hooks.renameThread).toHaveBeenCalledWith('t1', 'Кубер')
    })

    it('Esc отменяет переименование', async () => {
      mocks.messages = []
      mocks.proposals = []
      const hooks = await import('../api/hooks')
      vi.mocked(hooks.renameThread).mockClear()
      renderPage()

      openMenu('Про Kubernetes')
      fireEvent.click(screen.getByRole('menuitem', { name: 'Переименовать' }))
      const input = screen.getByRole('textbox', { name: 'Название разговора' })
      fireEvent.change(input, { target: { value: 'Кубер' } })
      await act(async () => {
        fireEvent.keyDown(input, { key: 'Escape' })
      })
      expect(screen.queryByRole('textbox', { name: 'Название разговора' })).toBeNull()
      expect(hooks.renameThread).not.toHaveBeenCalled()
    })

    it('удаление чужого разговора спрашивает и оставляет открытый на месте', async () => {
      mocks.messages = []
      mocks.proposals = []
      const hooks = await import('../api/hooks')
      vi.mocked(hooks.deleteThread)
        .mockClear()
        .mockResolvedValue({ messages: 0, proposals: 0 })
      renderPage('/chat/t1')

      openMenu('Python разработчик — Ромашка')
      fireEvent.click(screen.getByRole('menuitem', { name: 'Удалить' }))
      expect(hooks.deleteThread).not.toHaveBeenCalled()
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: 'Удалить', hidden: true }))
      })
      expect(hooks.deleteThread).toHaveBeenCalledWith('t2')
      expect(screen.getByTestId('where').textContent).toBe('/chat/t1')
    })
  })
})
