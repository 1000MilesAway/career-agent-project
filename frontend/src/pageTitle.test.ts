import { describe, expect, it } from 'vitest'
import { pageTitle } from './pageTitle'

describe('pageTitle', () => {
  it('names the cabinet page before the site', () => {
    expect(pageTitle('/search', true)).toBe('Поиск вакансий | Career Helper')
    expect(pageTitle('/resume', true)).toBe('Моё резюме | Career Helper')
  })

  it('gives the landing the bare site name', () => {
    expect(pageTitle('/', false)).toBe('Career Helper')
  })

  it('calls every closed path the login form when logged out', () => {
    expect(pageTitle('/search', false)).toBe('Вход | Career Helper')
    expect(pageTitle('/login', false)).toBe('Вход | Career Helper')
  })

  it('keeps the open pages their own titles when logged out', () => {
    expect(pageTitle('/terms', false)).toBe('Пользовательское соглашение | Career Helper')
    expect(pageTitle('/verify', false)).toBe('Подтверждение почты | Career Helper')
    expect(pageTitle('/reset-password', false)).toBe('Новый пароль | Career Helper')
  })

  it('falls back to the site name for a path about to redirect', () => {
    expect(pageTitle('/nowhere', true)).toBe('Career Helper')
  })
})
