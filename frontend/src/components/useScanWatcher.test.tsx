/** Итог поиска сообщается там, где пользователь сейчас, — и только новый.
 *
 * Наблюдатель живёт в оболочке кабинета: пока идёт поиск, можно уйти на
 * другую страницу. Поиск, закончившийся до открытия вкладки, — не новость.
 */

import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ScanJob } from '../api/types'

const mocks = vi.hoisted(() => ({
  job: null as unknown,
  mutate: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  isActive: (job: { status?: string } | null) =>
    job?.status === 'queued' || job?.status === 'running',
  useScanJob: () => ({ job: mocks.job }),
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))

// Stable, as the real provider's memoised value is.
const toast = { info: mocks.info, error: mocks.error }
vi.mock('./toast', () => ({ useToast: () => toast }))

const { useScanWatcher } = await import('./useScanWatcher')

function job(status: ScanJob['status'], extra: Partial<ScanJob> = {}): ScanJob {
  return {
    id: 'j1',
    kind: 'scan',
    status,
    attempts: 1,
    result: null,
    error: null,
    created_at: '2026-09-28T10:00:00Z',
    finished_at: null,
    ...extra,
  }
}

describe('useScanWatcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('закончившийся поиск обновляет список и говорит, сколько нового', () => {
    mocks.job = job('running')
    const { rerender } = renderHook(() => useScanWatcher())
    mocks.job = job('done', {
      result: { collected: 10, dropped: 5, new: 3 },
    })
    rerender()

    expect(mocks.mutate).toHaveBeenCalledWith('/api/state')
    expect(mocks.info).toHaveBeenCalledWith('Поиск закончен: новых вакансий 3.')
  })

  it('упавший поиск назван с причиной', () => {
    mocks.job = job('queued')
    const { rerender } = renderHook(() => useScanWatcher())
    mocks.job = job('failed', { error: 'профиль поиска пуст' })
    rerender()

    expect(mocks.error).toHaveBeenCalledWith('Поиск не удался: профиль поиска пуст')
  })

  it('старый итог при открытии вкладки — не новость', () => {
    mocks.job = null
    const { rerender } = renderHook(() => useScanWatcher())
    mocks.job = job('done')
    rerender()
    rerender()

    expect(mocks.info).not.toHaveBeenCalled()
    expect(mocks.mutate).not.toHaveBeenCalled()
  })
})
