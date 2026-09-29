/** Кнопки выгрузки и конструктор оформления.
 *
 * Что здесь закреплено: превью берётся с сервера в выбранном стиле, выбор
 * стиля доходит до файла, и быстрые кнопки после этого качают в том же стиле.
 * Иначе пользователь выбрал бы «Акцент», закрыл окно и получил классический
 * лист — и решил бы, что выбор не работает.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DownloadButtons } from './DownloadButtons'
import { ToastProvider } from './toast'

const STYLES = [
  { id: 'classic', name: 'Классический', description: 'Спокойный лист' },
  { id: 'accent', name: 'Акцент', description: 'Оранжевая плашка' },
]

function mockApi() {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (url === '/api/export/styles') return new Response(JSON.stringify(STYLES))
    if (url.includes('.html?')) return new Response(`<html><body>${url}</body></html>`)
    return new Response(new Blob(['%PDF']), {
      headers: { 'Content-Disposition': 'attachment' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function renderButtons() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ToastProvider>
        <DownloadButtons exportBase="/api/export/resume" label="Скачать резюме" />
      </ToastProvider>
    </SWRConfig>,
  )
}

beforeEach(() => {
  localStorage.clear()
  // jsdom не реализует ни showModal, ни ResizeObserver, ни blob-ссылки.
  // Атрибут open ставится руками: без него jsdom считает содержимое
  // закрытого диалога невидимым, и роли внутри не находятся.
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '')
  })
  HTMLDialogElement.prototype.close = vi.fn()
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  )
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})

describe('DownloadButtons', () => {
  it('по умолчанию качает в классическом стиле', async () => {
    const calls = mockApi()
    renderButtons()

    fireEvent.click(screen.getByRole('button', { name: 'Скачать резюме — PDF' }))

    await waitFor(() => expect(calls).toContain('/api/export/resume.pdf?style=classic'))
  })

  it('конструктор показывает превью выбранного стиля и запоминает выбор', async () => {
    const calls = mockApi()
    renderButtons()

    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать резюме — выбрать оформление' }),
    )
    await waitFor(() => expect(calls).toContain('/api/export/resume.html?style=classic'))

    fireEvent.click(await screen.findByRole('radio', { name: /Акцент/ }))
    await waitFor(() => expect(calls).toContain('/api/export/resume.html?style=accent'))
    expect(localStorage.getItem('resume-style')).toBe('accent')

    // Быстрая кнопка вне окна качает в только что выбранном стиле.
    fireEvent.click(screen.getByRole('button', { name: 'Скачать резюме — DOCX' }))
    await waitFor(() => expect(calls).toContain('/api/export/resume.docx?style=accent'))
  })

  it('превью в песочнице без скриптов', async () => {
    mockApi()
    renderButtons()

    fireEvent.click(
      screen.getByRole('button', { name: 'Скачать резюме — выбрать оформление' }),
    )
    const frame = await screen.findByTitle('Превью резюме')

    expect(frame.getAttribute('sandbox')).not.toContain('allow-scripts')
  })

  it('забытый сервером стиль не ломает выгрузку', async () => {
    localStorage.setItem('resume-style', 'neon')
    const calls = mockApi()
    renderButtons()

    // Список стилей должен успеть прийти: до него неизвестно, что «neon» пропал.
    await waitFor(() => expect(calls).toContain('/api/export/styles'))
    await new Promise((r) => setTimeout(r, 0))
    fireEvent.click(screen.getByRole('button', { name: 'Скачать резюме — PDF' }))

    await waitFor(() => expect(calls).toContain('/api/export/resume.pdf?style=classic'))
    expect(calls.some((c) => c.includes('style=neon'))).toBe(false)
  })
})
