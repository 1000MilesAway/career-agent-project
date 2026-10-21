/** Фильтры списка переживают перезагрузку страницы.
 *
 * Раньше «стоит смотреть / может быть / без вердикта» жили только в памяти
 * компонента, и F5 молча возвращал их к умолчаниям.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FoundVacancy } from '../api/types'
import { bucketOf } from '../components/VacancyRow'
import {
  DEFAULT_CONDITIONS,
  DEFAULT_FILTERS,
  type SearchFilters,
  cityMatches,
  conditionMiss,
  splitCities,
  loadFilters,
  saveFilters,
  sortRows,
} from './searchFilters'

const STORAGE_KEY = 'search-filters'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('searchFilters', () => {
  it('без сохранённого — умолчания', () => {
    expect(loadFilters()).toEqual(DEFAULT_FILTERS)
    expect(DEFAULT_FILTERS.buckets).toEqual(['worth'])
  })

  it('сохранённый выбор возвращается, включая «без вердикта»', () => {
    const chosen = {
      buckets: ['skip', null],
      sort: 'score',
      hideScored: true,
      showDropped: true,
      showInactive: true,
      showHidden: false,
      showMismatch: true,
      conditions: {
        salary: 250_000,
        anySalary: false,
        remoteOnly: false,
        accreditedOnly: true,
        // '' is a choice too — "any city" — and must not come back as null.
        city: '',
        includeGlobal: true,
        // Two of six: the choice must come back as it was stored, not as
        // "everything" because a missing key used to mean that.
        sources: ['hh', 'telegram'],
      },
    } satisfies SearchFilters
    saveFilters(chosen)
    expect(loadFilters()).toEqual(chosen)
  })

  it('старый ключ «только оценённые» не читается: смысл флага перевёрнут', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...DEFAULT_FILTERS, scoredOnly: true }),
    )
    expect(loadFilters().hideScored).toBe(false)
  })

  it('пустой выбор — тоже выбор, а не повод вернуть умолчания', () => {
    saveFilters({ ...DEFAULT_FILTERS, buckets: [] })
    expect(loadFilters().buckets).toEqual([])
  })

  it('мусор в хранилище даёт умолчания, неизвестная корзина отбрасывается', () => {
    localStorage.setItem('search-filters', '{not json')
    expect(loadFilters()).toEqual(DEFAULT_FILTERS)

    localStorage.setItem('search-filters', JSON.stringify({ buckets: ['worth', 'gone'] }))
    // A value saved before the inactive/hidden toggles or the sort existed
    // reads as the default.
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

const row = (id: string, score: number | null, bucket?: 'worth' | 'maybe') =>
  ({
    id,
    score,
    triage: bucket ? { bucket, reason: '' } : null,
  }) as unknown as FoundVacancy

describe('sortRows', () => {
  const rows = [
    row('a', null, 'maybe'),
    row('b', 3.1),
    row('c', null, 'worth'),
    row('d', 4.2),
  ]

  it('«свежие» — порядок сервера как есть', () => {
    expect(sortRows(rows, 'fresh').map((r) => r.id)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('«по баллу» — оценённые сверху от лучшего, дальше «стоит посмотреть» раньше «может быть»', () => {
    expect(sortRows(rows, 'score').map((r) => r.id)).toEqual(['d', 'b', 'c', 'a'])
  })
})

describe('bucketOf', () => {
  const vacancy = (patch: Partial<FoundVacancy>) =>
    ({ triage: null, verdict: null, ...patch }) as FoundVacancy

  it('оценённая вакансия без triage не попадает в «без вердикта»', () => {
    expect(bucketOf(vacancy({ verdict: 'apply' }))).toBe('worth')
    expect(bucketOf(vacancy({ verdict: 'apply_with_caveats' }))).toBe('maybe')
    expect(bucketOf(vacancy({ verdict: 'skip' }))).toBe('skip')
  })

  it('полная оценка перевешивает дешёвый отсев', () => {
    const row = vacancy({ verdict: 'apply', triage: { bucket: 'skip', reason: '' } })
    expect(bucketOf(row)).toBe('worth')
  })

  it('без оценки — вердикт triage, без обоих — null', () => {
    expect(bucketOf(vacancy({ triage: { bucket: 'maybe', reason: '' } }))).toBe('maybe')
    expect(bucketOf(vacancy({}))).toBeNull()
  })
})

describe('conditionMiss', () => {
  const vacancy = (fields: Partial<FoundVacancy>) =>
    ({ remote: null, accredited: null, salary_range: null, ...fields }) as FoundVacancy
  const at = (change: Partial<typeof DEFAULT_CONDITIONS>) => ({
    ...DEFAULT_CONDITIONS,
    ...change,
  })

  it('режет только явное «нет», молчание источника проходит', () => {
    const c = at({ remoteOnly: true, accreditedOnly: true })
    expect(conditionMiss(vacancy({}), c)).toBeNull()
    expect(conditionMiss(vacancy({ remote: false }), c)).toBe('не удалённая работа')
    expect(conditionMiss(vacancy({ accredited: false }), c)).toBe(
      'ИТ-аккредитация не подтверждена',
    )
  })

  it('вилка сравнивается по верхней границе, как в PreScreen', () => {
    const c = at({ salary: 200_000 })
    expect(conditionMiss(vacancy({ salary_range: [150_000, 220_000] }), c)).toBeNull()
    expect(conditionMiss(vacancy({ salary_range: [100_000, 180_000] }), c)).toMatch(
      /^зарплата ниже/,
    )
    expect(conditionMiss(vacancy({ salary_range: [180_000, null] }), c)).toMatch(
      /^зарплата ниже/,
    )
  })

  it('без указанной зарплаты — по галочке', () => {
    expect(conditionMiss(vacancy({}), at({ salary: 200_000, anySalary: true }))).toBeNull()
    expect(conditionMiss(vacancy({}), at({ salary: 200_000, anySalary: false }))).toBe(
      'зарплата не указана',
    )
    // Без порога галочка ни на что не влияет.
    expect(conditionMiss(vacancy({}), at({ salary: null, anySalary: false }))).toBeNull()
  })

  it('чужой город сворачивается с его названием, удалёнка и молчание проходят', () => {
    const c = at({ remoteOnly: false, city: 'Москва' })
    expect(conditionMiss(vacancy({ location: 'Казань', remote: false }), c)).toBe(
      'другой город: Казань',
    )
    expect(conditionMiss(vacancy({ location: 'Москва, м. Курская' }), c)).toBeNull()
    expect(conditionMiss(vacancy({ location: 'Казань', remote: true }), c)).toBeNull()
    expect(conditionMiss(vacancy({ location: '' }), c)).toBeNull()
    expect(conditionMiss(vacancy({ location: 'Казань' }), at({ city: '' }))).toBeNull()
  })
})

describe('cityMatches', () => {
  it('понимает разные написания одного города', () => {
    expect(cityMatches('Санкт-Петербург', ['СПб'])).toBe(true)
    expect(cityMatches('Saint Petersburg', ['питер'])).toBe(true)
    expect(cityMatches('г. Москва', ['москва'])).toBe(true)
    expect(cityMatches('Москва', ['Казань', 'Москва'])).toBe(true)
    expect(cityMatches('Москва', ['Казань'])).toBe(false)
    expect(cityMatches('Новомосковск', ['Москва'])).toBe(false)
  })

  it('города вводятся через запятую', () => {
    expect(splitCities(' Москва, ,Казань ')).toEqual(['Москва', 'Казань'])
    expect(splitCities(null)).toEqual([])
  })
})
