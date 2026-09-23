/** Экран поиска: одна кнопка на все источники и отчёт по каждому.
 *
 * «Страниц» на экране больше нет: страница значила разное в каждом источнике
 * и ничего не говорила пользователю. Упавший источник называется по имени,
 * а не растворяется в меньшей сумме.
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ScanResult } from '../api/types'

const mocks = vi.hoisted(() => ({
  scanVacancies: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  useAppState: () => ({
    data: {
      has_resume: true,
      found: [],
      apply_steps: [],
      spend: { found: 0, scored: 0, with_artifacts: 0, rub: 0, free_to_reopen: 0 },
    },
    isLoading: false,
    error: null,
  }),
  scanVacancies: mocks.scanVacancies,
  runTriage: vi.fn(),
  clearFound: vi.fn(),
  setSaved: vi.fn(),
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

describe('SearchPage', () => {
  it('одна кнопка ищет везде, поля «страниц» нет', async () => {
    mocks.scanVacancies.mockResolvedValue(result)
    render(<SearchPage />)

    expect(screen.queryByText(/страниц/)).toBeNull()
    const button = screen.getByRole('button', { name: 'Найти вакансии' })
    await act(async () => {
      fireEvent.click(button)
    })

    expect(mocks.scanVacancies).toHaveBeenCalledTimes(1)
    expect(mocks.scanVacancies.mock.calls[0][0]).toMatchObject({
      source: 'all',
      include_global: false,
    })
    expect(mocks.scanVacancies.mock.calls[0][0]).not.toHaveProperty('pages')
  })

  it('итог показан по каждому источнику, упавший назван с причиной', async () => {
    mocks.scanVacancies.mockResolvedValue(result)
    render(<SearchPage />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Найти вакансии' }))
    })

    expect(screen.getByText(/Хабр Карьера: собрано 100/)).toBeTruthy()
    expect(screen.getByText(/Сайты компаний: не прочитан — ConnectError/)).toBeTruthy()
    expect(
      screen.getByText(/Telegram-каналы: .*уже найдены в другом месте 5.*пропущено: 1/),
    ).toBeTruthy()
  })
})
