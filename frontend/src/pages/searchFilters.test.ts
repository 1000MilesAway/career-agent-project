/** Фильтры списка переживают перезагрузку страницы.
 *
 * Раньше «стоит смотреть / может быть / без вердикта» жили только в памяти
 * компонента, и F5 молча возвращал их к умолчаниям.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_FILTERS, loadFilters, saveFilters } from './searchFilters'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('searchFilters', () => {
  it('без сохранённого — умолчания', () => {
    expect(loadFilters()).toEqual(DEFAULT_FILTERS)
  })

  it('сохранённый выбор возвращается, включая «без вердикта»', () => {
    const chosen = {
      buckets: ['skip', null],
      showDropped: true,
      showInactive: true,
      showHidden: false,
    } as const
    saveFilters({ ...chosen, buckets: [...chosen.buckets] })
    expect(loadFilters()).toEqual(chosen)
  })

  it('пустой выбор — тоже выбор, а не повод вернуть умолчания', () => {
    saveFilters({ ...DEFAULT_FILTERS, buckets: [] })
    expect(loadFilters().buckets).toEqual([])
  })

  it('мусор в хранилище даёт умолчания, неизвестная корзина отбрасывается', () => {
    localStorage.setItem('search-filters', '{not json')
    expect(loadFilters()).toEqual(DEFAULT_FILTERS)

    localStorage.setItem('search-filters', JSON.stringify({ buckets: ['worth', 'gone'] }))
    // A value saved before the inactive/hidden toggles existed reads as off.
    expect(loadFilters()).toEqual({ ...DEFAULT_FILTERS, buckets: ['worth'] })
  })

  it('закрытое хранилище не ломает страницу', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => saveFilters(DEFAULT_FILTERS)).not.toThrow()
    expect(loadFilters()).toEqual(DEFAULT_FILTERS)
  })
})
