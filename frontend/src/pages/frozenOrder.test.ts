/** Порядок списка не прыгает сам: изменения копятся до «Пересортировать». */

import { describe, expect, it } from 'vitest'
import type { FoundVacancy } from '../api/types'
import { arrange, pendingChanges } from './frozenOrder'

const row = (id: string) => ({ id }) as FoundVacancy

describe('arrange', () => {
  it('держит порядок, новые строки — в конец, стёртые из выдачи — прочь', () => {
    const rows = [row('a'), row('b'), row('c'), row('new')]
    const live = [row('new'), row('c'), row('a')]
    // «b» left the filters but is still in the payload; «gone» was cleared.
    const ids = arrange(['a', 'b', 'gone', 'c'], rows, live).map((r) => r.id)
    expect(ids).toEqual(['a', 'b', 'c', 'new'])
  })
})

describe('pendingChanges', () => {
  it('совпадает с живым порядком — изменений нет', () => {
    expect(pendingChanges(['a', 'b', 'c'], ['a', 'b', 'c'])).toBe(0)
  })

  it('одна оценённая строка уехала бы наверх — это одно изменение, а не три', () => {
    expect(pendingChanges(['a', 'b', 'c', 'd'], ['d', 'a', 'b', 'c'])).toBe(1)
  })

  it('строка ушла из фильтра — одно изменение', () => {
    expect(pendingChanges(['a', 'b', 'c'], ['a', 'c'])).toBe(1)
  })

  it('новые строки уже на своём месте в конце — не изменение', () => {
    expect(pendingChanges(['a', 'b', 'new'], ['a', 'b', 'new'])).toBe(0)
  })
})
