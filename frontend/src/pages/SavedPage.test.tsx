/** Избранное: вакансию переносят на другой этап, перетащив строку на шаг
 * воронки. Селект в строке остаётся — на телефоне перетаскивание не работает.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
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
        none: { title: 'не решено', terminal: false },
        applied: { title: 'откликнулся', terminal: false },
        interview: { title: 'интервью', terminal: false },
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
    rub: null,
    ...extra,
  }
}

/** jsdom has no DataTransfer; this is the part of it the page touches. */
function dataTransfer() {
  const store = new Map<string, string>()
  return {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? '',
    get types() {
      return [...store.keys()]
    },
    effectAllowed: 'none',
    dropEffect: 'none',
  }
}

function stage(title: string) {
  return screen.getByText(title, { selector: '.funnel__label' }).closest('button')!
}

function drag(rowTitle: string, stageTitle: string) {
  const source = screen.getByText(rowTitle).closest('.drag')!
  const target = stage(stageTitle)
  const transfer = dataTransfer()
  fireEvent.dragStart(source, { dataTransfer: transfer })
  fireEvent.dragOver(target, { dataTransfer: transfer })
  fireEvent.drop(target, { dataTransfer: transfer })
  fireEvent.dragEnd(source, { dataTransfer: transfer })
}

describe('SavedPage', () => {
  beforeEach(() => {
    mocks.moveVacancy.mockReset()
    mocks.found.splice(0, mocks.found.length, row('a'), row('b', { status: 'applied' }))
  })

  it('строка, брошенная на этап, переезжает на него', () => {
    render(<SavedPage />)
    drag('Вакансия a', 'откликнулся')
    expect(mocks.moveVacancy).toHaveBeenCalledWith('a', 'applied')
  })

  it('пустой этап принимает перетаскивание, хотя без него выключен', async () => {
    render(<SavedPage />)
    expect(stage('интервью').disabled).toBe(true)

    const source = screen.getByText('Вакансия a').closest('.drag')!
    fireEvent.dragStart(source, { dataTransfer: dataTransfer() })
    await act(() => new Promise((resolve) => setTimeout(resolve)))
    expect(stage('интервью').disabled).toBe(false)

    fireEvent.dragEnd(source)
    expect(stage('интервью').disabled).toBe(true)
  })

  it('на свой же этап — ничего не пишет', () => {
    render(<SavedPage />)
    drag('Вакансия b', 'откликнулся')
    expect(mocks.moveVacancy).not.toHaveBeenCalled()
  })

  it('чужое перетаскивание (текст, ссылка) не принимается', () => {
    render(<SavedPage />)
    const transfer = dataTransfer()
    transfer.setData('text/plain', 'a')
    fireEvent.drop(stage('откликнулся'), { dataTransfer: transfer })
    expect(mocks.moveVacancy).not.toHaveBeenCalled()
  })
})
