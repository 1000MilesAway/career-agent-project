/** «Мои вакансии»: ссылка или текст добавляют вакансию; список — только добавленное. */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FoundVacancy } from '../api/types'

const mocks = vi.hoisted(() => ({
  addVacancy: vi.fn(),
  deleteVacancy: vi.fn(),
  saveVacancy: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
  found: [] as unknown[],
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  useAppState: () => ({
    data: { found: mocks.found, apply_steps: [], statuses: {} },
    isLoading: false,
    error: null,
  }),
  addVacancy: mocks.addVacancy,
  deleteVacancy: mocks.deleteVacancy,
  saveVacancy: mocks.saveVacancy,
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: vi.fn() }) }))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: mocks.info, error: mocks.error }),
}))

vi.mock('../components/useDocumentPanel', () => ({
  useDocumentPanel: () => ({ openReport: vi.fn(), isBusy: () => false, panel: null }),
}))

const { AddedPage } = await import('./AddedPage')

function row(id: string, extra: Partial<FoundVacancy> = {}): FoundVacancy {
  return {
    id,
    url: null,
    title: `Вакансия ${id}`,
    company: null,
    qualification: null,
    salary_text: null,
    remote: null,
    source: 'text',
    stage: 'shortlist',
    screen_reason: null,
    triage: null,
    saved: false,
    status: null,
    score: null,
    verdict: null,
    report: null,
    artifacts: null,
    added_at: '2026-10-07T10:00:00+00:00',
    ...extra,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AddedPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.found = []
})

describe('AddedPage', () => {
  it('lists only what the user added, newest first', () => {
    mocks.found = [
      row('old', { added_at: '2026-10-01T10:00:00+00:00' }),
      row('scan', { added_at: null, source: 'habr' }),
      row('new', { added_at: '2026-10-07T10:00:00+00:00' }),
    ]
    renderPage()
    const titles = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(titles).toEqual(['Вакансия new', 'Вакансия old'])
  })

  it('sends the pasted link and clears the field', async () => {
    mocks.addVacancy.mockResolvedValue({ vacancy: { id: 'x' }, created: true })
    renderPage()
    const field = screen.getByLabelText('Ссылка на вакансию или её текст')
    fireEvent.change(field, { target: { value: '  https://career.habr.com/vacancies/1 ' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    })
    expect(mocks.addVacancy).toHaveBeenCalledWith('https://career.habr.com/vacancies/1')
    expect(mocks.info).toHaveBeenCalledWith('Вакансия добавлена.')
    expect((field as HTMLTextAreaElement).value).toBe('')
  })

  it('keeps the text when the server cannot read it', async () => {
    mocks.addVacancy.mockRejectedValue(new Error('Слишком короткий текст'))
    renderPage()
    const field = screen.getByLabelText('Ссылка на вакансию или её текст')
    fireEvent.change(field, { target: { value: 'Python' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Добавить' }))
    })
    expect(mocks.error).toHaveBeenCalled()
    expect((field as HTMLTextAreaElement).value).toBe('Python')
  })

  it('stars a vacancy into the shortlist', async () => {
    mocks.found = [row('a')]
    renderPage()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'В избранное' }))
    })
    expect(mocks.saveVacancy).toHaveBeenCalledWith('a', true)
  })

  it('deletes only after the confirmation', async () => {
    mocks.found = [row('a')]
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Удалить' }))
    expect(mocks.deleteVacancy).not.toHaveBeenCalled()
    const confirm = screen.getAllByRole('button', { name: 'Удалить', hidden: true }).at(-1)!
    await act(async () => {
      fireEvent.click(confirm)
    })
    expect(mocks.deleteVacancy).toHaveBeenCalledWith('a')
  })
})
