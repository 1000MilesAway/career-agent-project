/** Итог умного отбора сообщается там, где пользователь сейчас, — и только новый.
 *
 * Отбор — задача на сервере, как поиск: пока модель думает, кабинет не
 * держится. Порядок списка пересобирается, только когда вердикты уже в строках.
 */

import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TriageJob } from '../api/types'

const mocks = vi.hoisted(() => ({
  job: null as unknown,
  mutate: vi.fn(),
  settled: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  isActive: (job: { status?: string } | null) =>
    job?.status === 'queued' || job?.status === 'running',
  useTriageJob: () => ({ job: mocks.job }),
  markTriageSettled: mocks.settled,
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))

const toast = { info: mocks.info, error: mocks.error }
vi.mock('./toast', () => ({ useToast: () => toast }))

const { useTriageWatcher } = await import('./useTriageWatcher')

function job(status: TriageJob['status'], extra: Partial<TriageJob> = {}): TriageJob {
  return {
    id: 't1',
    kind: 'triage',
    status,
    attempts: 1,
    result: null,
    error: null,
    created_at: '2026-10-03T10:00:00Z',
    finished_at: null,
    ...extra,
  }
}

describe('useTriageWatcher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.mutate.mockResolvedValue(undefined)
  })

  it('закончившийся отбор обновляет список, потом пересобирает порядок', async () => {
    mocks.job = job('running')
    const { rerender } = renderHook(() => useTriageWatcher())
    mocks.job = job('done', {
      finished_at: '2026-10-03T10:01:00Z',
      result: { judged: 4, missing: [] },
    })
    rerender()

    expect(mocks.mutate).toHaveBeenCalledWith('/api/state')
    expect(mocks.info).toHaveBeenCalledWith('Умный отбор: разобрано 4.')
    await Promise.resolve()
    expect(mocks.settled).toHaveBeenCalledWith('2026-10-03T10:01:00Z')
  })

  it('несуждённые названы, а не молча отсеяны', () => {
    mocks.job = job('running')
    const { rerender } = renderHook(() => useTriageWatcher())
    mocks.job = job('done', { result: { judged: 2, missing: ['a', 'b'] } })
    rerender()

    expect(mocks.error).toHaveBeenCalledWith('Без вердикта осталось 2.')
  })

  it('упавший отбор назван с причиной', () => {
    mocks.job = job('queued')
    const { rerender } = renderHook(() => useTriageWatcher())
    mocks.job = job('failed', { error: 'Лимит на сегодня исчерпан' })
    rerender()

    expect(mocks.error).toHaveBeenCalledWith(
      'Умный отбор не удался: Лимит на сегодня исчерпан',
    )
    expect(mocks.mutate).not.toHaveBeenCalled()
  })

  it('старый итог при открытии вкладки — не новость', () => {
    mocks.job = null
    const { rerender } = renderHook(() => useTriageWatcher())
    mocks.job = job('done', { result: { judged: 1 } })
    rerender()

    expect(mocks.info).not.toHaveBeenCalled()
  })
})
