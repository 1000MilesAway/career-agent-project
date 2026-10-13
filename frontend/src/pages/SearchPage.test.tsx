/** Экран поиска: одна кнопка на все источники и отчёт по каждому.
 *
 * Поиск — задача на сервере: кнопка ставит её в очередь и сразу отпускает
 * страницу, а итог читается с задачи — его видно и после ухода со страницы.
 *
 * «Страниц» на экране больше нет: страница значила разное в каждом источнике
 * и ничего не говорила пользователю. Упавший источник называется по имени,
 * а не растворяется в меньшей сумме.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FoundVacancy, ScanJob, ScanResult } from '../api/types'

const mocks = vi.hoisted(() => ({
  startScan: vi.fn(),
  hideVacancy: vi.fn(),
  startTriage: vi.fn(),
  clearFound: vi.fn(),
  found: [] as unknown[],
  scanJob: null as unknown,
  refreshJob: null as unknown,
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  useAppState: () => ({
    data: {
      has_resume: true,
      found: mocks.found,
      apply_steps: [],
      spend: {
        found: 0,
        scored: 0,
        with_artifacts: 0,
        quota: { plan: 'free', paid_until: null, day: null, week: null },
      },
    },
    isLoading: false,
    error: null,
  }),
  SCAN_JOB_KEY: '/api/jobs?kind=scan&limit=1',
  isActive: (job: { status?: string } | null) =>
    job?.status === 'queued' || job?.status === 'running',
  useScanJob: () => ({ job: mocks.scanJob }),
  useRefreshJob: () => ({ job: mocks.refreshJob }),
  TRIAGE_JOB_KEY: '/api/jobs?kind=triage&limit=1',
  useTriageJob: () => ({ job: null }),
  useTriageSettled: () => null,
  startScan: mocks.startScan,
  startTriage: mocks.startTriage,
  clearFound: mocks.clearFound,
  saveVacancy: vi.fn(),
  hideVacancy: mocks.hideVacancy,
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: vi.fn() }) }))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn() }),
}))

vi.mock('../components/useDocumentPanel', () => ({
  useDocumentPanel: () => ({ openReport: vi.fn(), isBusy: () => false, panel: null }),
}))

const { SearchPage } = await import('./SearchPage')

const result: ScanResult = {
  collected: 300,
  kept: 40,
  dropped: 255,
  duplicates: 5,
  new: 38,
  sources: {
    habr: { collected: 100, kept: 20, dropped: 80, duplicates: 0, new: 20 },
    company: { error: 'ConnectError: нет сети' },
    telegram: {
      collected: 200,
      kept: 20,
      dropped: 175,
      duplicates: 5,
      new: 18,
      problems: [{ channel: 'golangjob', why: 'молчит с 2023' }],
    },
  },
}

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
    saved: false,
    status: null,
    score: null,
    verdict: null,
    report: null,
    artifacts: null,
    ...extra,
  }
}

describe('SearchPage', () => {
  it('условия фильтруют и найденное: неподходящая строка уходит, ослабил условие — вернулась', () => {
    localStorage.clear()
    mocks.found.splice(
      0,
      mocks.found.length,
      row('remote', { remote: true }),
      row('office', { remote: false }),
    )
    render(<SearchPage />)

    // «Удалёнка» включена по умолчанию.
    expect(screen.getByText('Вакансия remote')).toBeTruthy()
    expect(screen.queryByText('Вакансия office')).toBeNull()

    expect(screen.queryByRole('button', { name: /не под условия/ })).toBeNull()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Удалёнка' }))
    expect(screen.getByText('Вакансия office')).toBeTruthy()
    mocks.found.length = 0
  })

  it('умный отбор получает только строки, прошедшие условия', async () => {
    localStorage.clear()
    mocks.startTriage.mockResolvedValue({ id: 't1', kind: 'triage', status: 'queued' })
    mocks.found.splice(
      0,
      mocks.found.length,
      row('remote', { remote: true }),
      row('office', { remote: false }),
      row('judged', { triage: { bucket: 'worth', reason: '' } }),
    )
    render(<SearchPage />)

    expect(screen.getByText('Умный отбор')).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Отобрать лучшие' }))
    })
    expect(mocks.startTriage).toHaveBeenCalledWith(['remote'])
    mocks.found.length = 0
  })

  it('«Очистить» сначала спрашивает и называет, что уцелеет', async () => {
    localStorage.clear()
    mocks.clearFound.mockResolvedValue({ deleted: 1 })
    mocks.found.splice(0, mocks.found.length, row('a'), row('b', { saved: true }))
    render(<SearchPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }))
    expect(mocks.clearFound).not.toHaveBeenCalled()
    expect(
      screen.getByText('Вы собираетесь удалить все найденные вакансии, кроме избранных.'),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Отмена', hidden: true }))
    expect(mocks.clearFound).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Удалить', hidden: true }))
    })
    expect(mocks.clearFound).toHaveBeenCalledOnce()
    mocks.found.length = 0
  })

  it('закрытые и старые не показываются, скрытые свёрнуты под переключателем', () => {
    localStorage.clear()
    mocks.found.splice(
      0,
      mocks.found.length,
      row('live'),
      row('closed', { closed: { at: '2026-09-27', reason: 'архив' } }),
      row('old', { stale: 'опубликована 40 дн. назад' }),
      row('hidden', { hidden: true }),
    )
    render(<SearchPage />)

    expect(screen.getByText('Вакансия live')).toBeTruthy()
    expect(screen.queryByText('Вакансия closed')).toBeNull()
    expect(screen.queryByText('Вакансия hidden')).toBeNull()

    // Hidden for now by the owner's call of 2026-10-02: no toggle brings them back.
    expect(screen.queryByText('Вакансия old')).toBeNull()
    expect(screen.queryByRole('button', { name: /неактуальные/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /показать скрытые \(1\)/ }))
    expect(screen.getByText('Вакансия hidden')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Вернуть' })).toBeTruthy()
    mocks.found.length = 0
  })

  it('«только оценённые» оставляет оценённые, «по баллу» ставит лучший первым', () => {
    localStorage.clear()
    mocks.found.splice(
      0,
      mocks.found.length,
      row('new'),
      row('low', { score: 2.8 }),
      row('high', { score: 4.2 }),
    )
    render(<SearchPage />)
    const titles = () => screen.getAllByText(/^Вакансия /).map((node) => node.textContent)

    expect(titles()).toEqual(['Вакансия new', 'Вакансия low', 'Вакансия high'])

    fireEvent.click(screen.getByRole('button', { name: 'по баллу' }))
    expect(titles()).toEqual(['Вакансия high', 'Вакансия low', 'Вакансия new'])

    fireEvent.click(screen.getByLabelText(/только оценённые/))
    expect(titles()).toEqual(['Вакансия high', 'Вакансия low'])
    mocks.found.length = 0
    localStorage.clear()
  })

  it('оценённая строка остаётся на месте, пока не нажато «Пересортировать»', () => {
    localStorage.clear()
    mocks.found.splice(0, mocks.found.length, row('a', { score: 4 }), row('b'), row('c'))
    const view = render(<SearchPage />)
    const titles = () => screen.getAllByText(/^Вакансия /).map((node) => node.textContent)
    fireEvent.click(screen.getByRole('button', { name: 'по баллу' }))
    expect(titles()).toEqual(['Вакансия a', 'Вакансия b', 'Вакансия c'])
    expect(screen.queryByRole('button', { name: 'Пересортировать' })).toBeNull()

    // A fresh payload after the evaluation: «c» now outscores «a», but the
    // list stays where the user left it. A new array, as SWR hands over:
    // mutating the old one in place would not reach the memoised list.
    mocks.found = [row('a', { score: 4 }), row('b'), row('c', { score: 4.5 })]
    view.rerender(<SearchPage />)
    expect(titles()).toEqual(['Вакансия a', 'Вакансия b', 'Вакансия c'])
    expect(screen.getByRole('status').textContent).toMatch(/изменений: 1/)

    fireEvent.click(screen.getByRole('button', { name: 'Пересортировать' }))
    expect(titles()).toEqual(['Вакансия c', 'Вакансия a', 'Вакансия b'])
    expect(screen.queryByRole('button', { name: 'Пересортировать' })).toBeNull()
    mocks.found.length = 0
    localStorage.clear()
  })

  it('скрытая строка не пропадает из-под руки, а бледнеет с кнопкой «Вернуть»', () => {
    localStorage.clear()
    mocks.found.splice(0, mocks.found.length, row('a'), row('b'))
    const view = render(<SearchPage />)
    mocks.found = [row('a', { hidden: true }), row('b')]
    view.rerender(<SearchPage />)

    expect(screen.getByText('Вакансия a')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Вернуть' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Пересортировать' }))
    expect(screen.queryByText('Вакансия a')).toBeNull()
    mocks.found.length = 0
  })

  it('«Скрыть» зовёт API, ничего не удаляя', async () => {
    mocks.found.splice(0, mocks.found.length, row('live'))
    mocks.hideVacancy.mockResolvedValue({})
    render(<SearchPage />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Скрыть' }))
    })
    expect(mocks.hideVacancy).toHaveBeenCalledWith('live', true)
    mocks.found.length = 0
  })

  it('одна кнопка ставит поиск по всем источникам, поля «страниц» нет', async () => {
    mocks.scanJob = null
    mocks.startScan.mockResolvedValue(job('queued'))
    render(<SearchPage />)

    expect(screen.queryByText(/страниц/)).toBeNull()
    const button = screen.getByRole('button', { name: 'Найти вакансии' })
    await act(async () => {
      fireEvent.click(button)
    })

    expect(mocks.startScan).toHaveBeenCalledTimes(1)
    expect(mocks.startScan.mock.calls[0][0]).toMatchObject({
      source: 'all',
      include_global: false,
    })
    expect(mocks.startScan.mock.calls[0][0]).not.toHaveProperty('pages')
  })

  it('пока поиск идёт, кнопка занята и сказано, что можно уйти', () => {
    mocks.scanJob = job('running')
    render(<SearchPage />)

    const button = screen.getByRole('button', { name: 'Ищу во всех источниках…' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/поиск продолжится/)).toBeTruthy()
    mocks.scanJob = null
  })

  it('итог показан по каждому источнику, упавший назван с причиной', () => {
    mocks.scanJob = job('done', { result })
    render(<SearchPage />)

    expect(screen.getByText(/Хабр Карьера: собрано 100/)).toBeTruthy()
    expect(screen.getByText(/Сайты компаний: не прочитан — ConnectError/)).toBeTruthy()
    expect(
      screen.getByText(/Telegram-каналы: .*уже найдены в другом месте 5.*пропущено: 1/),
    ).toBeTruthy()
    mocks.scanJob = null
  })

  it('«новых» считает строки списка, отмечает их и показывает по нажатию', () => {
    mocks.scanJob = job('done', { result })
    mocks.refreshJob = job('running', {
      id: 'r1',
      kind: 'refresh',
      created_at: '2026-09-28T10:01:00Z',
    })
    mocks.found = [
      row('old', { title: 'Старая', found_at: '2026-09-27T10:00:00Z' }),
      row('a', { title: 'Новая А', found_at: '2026-09-28T10:00:30Z' }),
      row('b', { title: 'Новая Б', found_at: '2026-09-28T10:02:00Z' }),
      row('c', { title: 'Закрытая', found_at: '2026-09-28T10:00:30Z', stale: 'давно' }),
    ]
    render(<SearchPage />)

    // The server said 38; the list holds two of them, a third is folded.
    const toggle = screen.getByRole('button', { name: /новых в списке 2/ })
    expect(document.querySelector('.scan__fresh')!.textContent).toContain(
      'ещё 1 закрыты или не под условия',
    )
    expect(screen.getByText(/Дочитываю источники/)).toBeTruthy()
    expect(document.querySelectorAll('.vrow__new')).toHaveLength(2)

    fireEvent.click(toggle)
    expect(screen.queryByText('Старая')).toBeNull()
    expect(screen.getByText('Новая А')).toBeTruthy()
    expect(screen.getByText('Новая Б')).toBeTruthy()

    mocks.scanJob = null
    mocks.refreshJob = null
    mocks.found = []
  })

  it('упавший поиск назван с причиной, а не выглядит пустым', () => {
    mocks.scanJob = job('failed', { error: 'профиль поиска пуст' })
    render(<SearchPage />)

    expect(screen.getByRole('alert').textContent).toContain('профиль поиска пуст')
    mocks.scanJob = null
  })
})

function job(status: ScanJob['status'], extra: Partial<ScanJob> = {}): ScanJob {
  return {
    id: 'j1',
    kind: 'scan',
    status,
    attempts: 1,
    result: null,
    error: null,
    created_at: '2026-09-28T10:00:00Z',
    finished_at: status === 'done' || status === 'failed' ? '2026-09-28T10:01:00Z' : null,
    ...extra,
  }
}
