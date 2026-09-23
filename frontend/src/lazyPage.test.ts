import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reloadOnce } from './lazyPage'

describe('reloadOnce', () => {
  const reload = vi.fn()

  beforeEach(() => {
    sessionStorage.clear()
    reload.mockReset()
    vi.stubGlobal('location', { reload })
  })
  afterEach(() => vi.unstubAllGlobals())

  it('reloads after a stale chunk, but not again right after that reload', () => {
    expect(reloadOnce(1_000_000)).toBe(true)
    expect(reloadOnce(1_005_000)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('reloads again for a later deploy in the same tab', () => {
    reloadOnce(1_000_000)
    expect(reloadOnce(1_000_000 + 60_000)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(2)
  })
})
