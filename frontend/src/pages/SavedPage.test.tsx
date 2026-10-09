/** Избранное: вакансию переносят на другой этап, перетащив строку на шаг
 * воронки. Мышью — после короткого сдвига, пальцем — долгим нажатием.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FoundVacancy } from '../api/types'

const mocks = vi.hoisted(() => ({
  moveVacancy: vi.fn(),
  found: [] as unknown[],
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  useAppState: () => ({
    data: {
      found: mocks.found,
      apply_steps: [],
      statuses: {
        none: { title: 'Не решено', terminal: false },
        applied: { title: 'Откликнулся', terminal: false },
        interview: { title: 'HR-интервью', terminal: false },
      },
    },
    isLoading: false,
    error: null,
  }),
  tailoredExportBase: () => '',
  recheckVacancies: vi.fn(),
  saveVacancy: vi.fn(),
  moveVacancy: mocks.moveVacancy,
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: vi.fn() }) }))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn() }),
}))

vi.mock('../components/useDocumentPanel', () => ({
  useDocumentPanel: () => ({
    openReport: vi.fn(),
    openArtifact: vi.fn(),
    isBusy: () => false,
    panel: null,
  }),
}))

const { SavedPage } = await import('./SavedPage')

function row(id: string, extra: Partial<FoundVacancy> = {}): FoundVacancy {
  return {
    id,
    url: null,
    title: `Вакансия ${id}`,
    company: null,
    qualification: null,
    salary_text: null,
    remote: null,
    source: 'habr',
    stage: 'shortlist',
    screen_reason: null,
    triage: null,
    saved: true,
    status: null,
    score: null,
    verdict: null,
    report: null,
    artifacts: null,
    ...extra,
  }
}

function stage(title: string) {
  return screen.getByText(title, { selector: '.funnel__label' }).closest('button')!
}

/** jsdom does no layout, so whatever the pointer is over is said outright. */
function pointAt(element: Element | null) {
  document.elementFromPoint = () => element
}

function press(rowTitle: string, pointerType = 'mouse') {
  const source = screen.getByText(rowTitle).closest('.drag')!
  fireEvent.pointerDown(source, { button: 0, pointerType, clientX: 100, clientY: 300 })
}

function carry(rowTitle: string, stageTitle: string) {
  press(rowTitle)
  pointAt(stage(stageTitle))
  fireEvent.pointerMove(window, { clientX: 120, clientY: 60 })
  fireEvent.pointerUp(window)
}

describe('SavedPage', () => {
  beforeEach(() => {
    mocks.moveVacancy.mockReset()
    mocks.found.splice(0, mocks.found.length, row('a'), row('b', { status: 'applied' }))
  })

  it('строка, брошенная на этап, переезжает на него', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    carry('Вакансия a', 'Откликнулся')
    expect(mocks.moveVacancy).toHaveBeenCalledWith('a', 'applied')
  })

  it('пока строку несут, под курсором её карточка, а курсор — рука', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    press('Вакансия a')
    pointAt(stage('HR-интервью'))
    fireEvent.pointerMove(window, { clientX: 120, clientY: 60 })

    expect(document.querySelector('.drag-ghost')?.textContent).toContain('Вакансия a')
    expect(document.documentElement.classList.contains('is-dragging')).toBe(true)
    expect(stage('HR-интервью').className).toContain('funnel__step--over')

    fireEvent.pointerUp(window)
    expect(document.querySelector('.drag-ghost')).toBeNull()
    expect(document.documentElement.classList.contains('is-dragging')).toBe(false)
    // An empty stage takes the row: that is the main reason to drag at all.
    expect(mocks.moveVacancy).toHaveBeenCalledWith('a', 'interview')
  })

  it('на свой же этап — ничего не пишет', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    carry('Вакансия b', 'Откликнулся')
    expect(mocks.moveVacancy).not.toHaveBeenCalled()
  })

  it('отпущенная мимо этапов или по Esc — остаётся где была', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    press('Вакансия a')
    pointAt(document.body)
    fireEvent.pointerMove(window, { clientX: 120, clientY: 60 })
    fireEvent.pointerUp(window)

    press('Вакансия a')
    pointAt(stage('Откликнулся'))
    fireEvent.pointerMove(window, { clientX: 120, clientY: 60 })
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.pointerUp(window)

    expect(mocks.moveVacancy).not.toHaveBeenCalled()
  })

  it('щелчок без движения — не перетаскивание', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    press('Вакансия a')
    pointAt(stage('Откликнулся'))
    fireEvent.pointerMove(window, { clientX: 102, clientY: 301 })
    fireEvent.pointerUp(window)
    expect(mocks.moveVacancy).not.toHaveBeenCalled()
  })

  it('палец, сдвинувшийся до долгого нажатия, листает, а не тащит', () => {
    vi.useFakeTimers()
    try {
      render(<SavedPage />, { wrapper: MemoryRouter })
      press('Вакансия a', 'touch')
      pointAt(stage('Откликнулся'))
      fireEvent.pointerMove(window, { clientX: 100, clientY: 200 })
      act(() => vi.advanceTimersByTime(500))
      fireEvent.pointerUp(window)
      expect(mocks.moveVacancy).not.toHaveBeenCalled()

      press('Вакансия a', 'touch')
      act(() => vi.advanceTimersByTime(500))
      fireEvent.pointerMove(window, { clientX: 120, clientY: 60 })
      fireEvent.pointerUp(window)
      expect(mocks.moveVacancy).toHaveBeenCalledWith('a', 'applied')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('SavedPage: статусы', () => {
  beforeEach(() => {
    mocks.found.splice(0, mocks.found.length, row('a'), row('b', { status: 'applied' }))
  })

  it('без фильтра нажат шаг «Все», а не первый этап', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    expect(stage('Все').getAttribute('aria-pressed')).toBe('true')
    expect(stage('Не решено').getAttribute('aria-pressed')).toBe('false')
  })

  it('статус виден и у неоценённой вакансии', () => {
    render(<SavedPage />, { wrapper: MemoryRouter })
    const values = screen
      .getAllByRole('combobox', { name: 'Статус отклика' })
      .map((select) => (select as HTMLButtonElement).value)
    expect(values).toEqual(['none', 'applied'])
  })
})
