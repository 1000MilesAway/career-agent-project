import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CoverLetterView } from './report'

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
}

/** jsdom has no execCommand; the stub reports what was selected at copy time. */
function stubExecCommand(result: boolean) {
  const copied: string[] = []
  Object.defineProperty(document, 'execCommand', {
    value: vi.fn(() => {
      copied.push(window.getSelection()?.toString() ?? '')
      return result
    }),
    configurable: true,
  })
  return copied
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('CoverLetterView copy', () => {
  it('copies the letter on screen', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    stubClipboard(writeText)
    render(<CoverLetterView value={{ letter: 'Второе письмо', angles: [] }} />)

    await act(async () => fireEvent.click(screen.getByRole('button')))

    expect(writeText).toHaveBeenCalledWith('Второе письмо')
    expect(screen.getByRole('button').textContent).toBe('Скопировано ✓')
  })

  it('falls back to the selection when the clipboard API rejects', async () => {
    stubClipboard(() => Promise.reject(new Error('Document is not focused.')))
    const copied = stubExecCommand(true)
    render(<CoverLetterView value={{ letter: 'Второе письмо', angles: [] }} />)

    await act(async () => fireEvent.click(screen.getByRole('button')))

    // The bug: a swallowed rejection left the previous letter in the clipboard.
    expect(copied).toEqual(['Второе письмо'])
    expect(screen.getByRole('button').textContent).toBe('Скопировано ✓')
  })

  it('says so when nothing could be copied', async () => {
    stubClipboard(() => Promise.reject(new Error('denied')))
    stubExecCommand(false)
    render(<CoverLetterView value={{ letter: 'Второе письмо', angles: [] }} />)

    await act(async () => fireEvent.click(screen.getByRole('button')))

    expect(screen.getByRole('button').textContent).toBe('Не скопировалось — Ctrl+C')
    expect(window.getSelection()?.toString()).toBe('Второе письмо')
  })
})

describe('CoverLetterView deliveries', () => {
  const both = { letter: 'Письмо к отклику', message: 'Пишу по вакансии', angles: [] }

  it('opens the preferred version and copies what is on screen', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    stubClipboard(writeText)
    render(<CoverLetterView value={both} prefer="message" />)

    expect(screen.getByText('Пишу по вакансии')).toBeTruthy()
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Скопировать' })))
    expect(writeText).toHaveBeenCalledWith('Пишу по вакансии')
  })

  it('switches to the board version', () => {
    render(<CoverLetterView value={both} prefer="message" />)

    fireEvent.click(screen.getByRole('button', { name: 'Отклик на сайте' }))

    expect(screen.getByText('Письмо к отклику')).toBeTruthy()
    expect(screen.queryByText('Пишу по вакансии')).toBeNull()
  })

  it('shows no switch on a letter bought before messages existed', () => {
    render(<CoverLetterView value={{ letter: 'Старое письмо', angles: [] }} prefer="message" />)

    expect(screen.getByText('Старое письмо')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Сообщение рекрутеру' })).toBeNull()
  })
})
