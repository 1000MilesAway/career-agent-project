import { describe, expect, it } from 'vitest'
import { pageTitle } from './pageTitle'

describe('pageTitle', () => {
  it('names the cabinet page before the site', () => {
    expect(pageTitle('/search', true)).toBe('Поиск вакансий | GetHire')
    expect(pageTitle('/resume', true)).toBe('Моё резюме | GetHire')
  })

  it('gives the landing the bare site name', () => {
    expect(pageTitle('/', false)).toBe('GetHire')
  })

  it('calls every closed path the login form when logged out', () => {
    expect(pageTitle('/search', false)).toBe('Вход | GetHire')
    expect(pageTitle('/login', false)).toBe('Вход | GetHire')
  })

  it('keeps the open pages their own titles when logged out', () => {
    expect(pageTitle('/terms', false)).toBe('Пользовательское соглашение | GetHire')
    expect(pageTitle('/verify', false)).toBe('Подтверждение почты | GetHire')
    expect(pageTitle('/reset-password', false)).toBe('Новый пароль | GetHire')
  })

  it('falls back to the site name for a path about to redirect', () => {
    expect(pageTitle('/nowhere', true)).toBe('GetHire')
  })
})
