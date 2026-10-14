import { describe, expect, it } from 'vitest'
import { relTime } from './relTime'

const now = new Date('2026-02-10T12:00:00Z')

function ago(seconds: number): string {
  return new Date(now.getTime() - seconds * 1000).toISOString()
}

describe('relTime', () => {
  it('нет даты — нет и надписи', () => {
    expect(relTime(null, now)).toBeNull()
    expect(relTime(undefined, now)).toBeNull()
    expect(relTime('', now)).toBeNull()
  })

  it('совсем свежее — «только что», включая перекос часов', () => {
    expect(relTime(ago(10), now)).toBe('только что')
    expect(relTime(ago(-30), now)).toBe('только что')
  })

  it('минуты с русской формой', () => {
    expect(relTime(ago(60), now)).toBe('1 минуту назад')
    expect(relTime(ago(2 * 60), now)).toBe('2 минуты назад')
    expect(relTime(ago(5 * 60), now)).toBe('5 минут назад')
    expect(relTime(ago(21 * 60), now)).toBe('21 минуту назад')
  })

  it('часы, дни, недели', () => {
    expect(relTime(ago(3 * 3600), now)).toBe('3 часа назад')
    expect(relTime(ago(25 * 3600), now)).toBe('1 день назад')
    expect(relTime(ago(5 * 86400), now)).toBe('5 дней назад')
    expect(relTime(ago(14 * 86400), now)).toBe('2 недели назад')
  })

  it('месяцы и годы за пределами недель', () => {
    expect(relTime(ago(40 * 86400), now)).toBe('1 месяц назад')
    expect(relTime(ago(200 * 86400), now)).toBe('6 месяцев назад')
    expect(relTime(ago(400 * 86400), now)).toBe('1 год назад')
    expect(relTime(ago(800 * 86400), now)).toBe('2 года назад')
  })
})