/** Звёздочка перерисовывается по клику, а не после ответа /api/state:
 * тот после поиска весит полмегабайта. Неудачное сохранение возвращает
 * звёздочку назад. */

import { act, renderHook } from '@testing-library/react'
import { mutate } from 'swr'
import { describe, expect, it, vi } from 'vitest'
import type { AppState } from './types'

const pending = vi.hoisted(() => ({
  settle: null as null | { resolve: () => void; reject: (e: Error) => void },
}))

vi.mock('./client', () => ({
  api: () =>
    new Promise<void>((resolve, reject) => {
      pending.settle = { resolve, reject }
    }),
  // Revalidation after the save asks for the state again; keep what is cached.
  fetcher: () => new Promise(() => {}),
  download: vi.fn(),
  fetchText: vi.fn(),
}))

const { STATE_KEY, hideVacancy, saveVacancy, useAppState } = await import('./hooks')

const state = {
  found: [
    { id: 'a', saved: false },
    { id: 'b', saved: false },
  ],
} as unknown as AppState

// Read the way a page reads it: what useAppState hands the screen.
async function shown() {
  const view = renderHook(() => useAppState())
  await act(async () => {})
  const found = view.result.current.data!.found.map((row) => row.saved)
  view.unmount()
  return found
}

describe('saveVacancy', () => {
  it('shows the star at once and keeps it when the save succeeds', async () => {
    await mutate(STATE_KEY, state, false)
    const saving = saveVacancy('a', true)
    expect(await shown()).toEqual([true, false])
    pending.settle!.resolve()
    await saving
    expect(await shown()).toEqual([true, false])
  })

  it('puts the star back when the save fails', async () => {
    await mutate(STATE_KEY, state, false)
    const saving = saveVacancy('b', true)
    expect(await shown()).toEqual([false, true])
    pending.settle!.reject(new Error('502'))
    await expect(saving).rejects.toThrow('502')
    expect(await shown()).toEqual([false, false])
  })

  it('folds a hidden row away at once too', async () => {
    await mutate(STATE_KEY, state, false)
    const hiding = hideVacancy('a', true)
    const view = renderHook(() => useAppState())
    await act(async () => {})
    expect(view.result.current.data!.found.map((row) => row.hidden)).toEqual([
      true,
      undefined,
    ])
    view.unmount()
    pending.settle!.resolve()
    await hiding
  })
})
