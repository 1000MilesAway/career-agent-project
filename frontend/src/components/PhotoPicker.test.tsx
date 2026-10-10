/** Карточка фото на «Моём резюме».
 *
 * Закреплено: на сервер уходит уменьшенная копия, а не исходный файл с
 * телефона (и его EXIF с геометкой).
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PhotoCard } from './PhotoPicker'
import { ToastProvider } from './toast'

const shrunk = new Blob(['small'], { type: 'image/jpeg' })
vi.mock('./shrinkPhoto', () => ({ shrinkPhoto: vi.fn(async () => shrunk) }))

function mockApi() {
  const calls: { url: string; method: string; body?: BodyInit | null }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body })
      if (url === '/api/resumes/photo' && (init?.method ?? 'GET') === 'GET')
        // A string body: jsdom's Blob is not the one Node's Response reads.
        return new Response('img', { headers: { 'Content-Type': 'image/png' } })
      if (url === '/api/state') return new Response(JSON.stringify({ has_photo: true }))
      return new Response(null, { status: 204 })
    }),
  )
  return calls
}

function renderCard(hasPhoto: boolean, editing = false) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ToastProvider>
        <PhotoCard hasPhoto={hasPhoto} editing={editing} />
      </ToastProvider>
    </SWRConfig>,
  )
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:photo')
  URL.revokeObjectURL = vi.fn()
})

describe('PhotoCard', () => {
  it('загружает уменьшенную копию и перечитывает состояние', async () => {
    const calls = mockApi()
    const { container } = renderCard(false)

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const original = new File(['huge'], 'IMG_0001.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [original] } })

    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.url).toBe('/api/resumes/photo')
    expect((put.body as FormData).get('file')).toBeInstanceOf(Blob)
    expect(((put.body as FormData).get('file') as File).size).toBe(shrunk.size)
  })

  it('брошенный в зону файл загружается сразу, без подтверждения', async () => {
    const calls = mockApi()
    renderCard(false)

    const zone = screen.getByText('Перетащите фото сюда').closest('label')!
    const original = new File(['huge'], 'IMG_0002.png', { type: 'image/png' })
    fireEvent.dragOver(zone)
    expect(zone.className).toContain('drop--over')
    fireEvent.drop(zone, { dataTransfer: { files: [original] } })

    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({ url: '/api/resumes/photo', method: 'PUT' }),
      ),
    )
    expect(zone.className).not.toContain('drop--over')
  })

  it('вне редактирования фото только показывается', async () => {
    mockApi()
    const { container } = renderCard(true)
    expect(await screen.findByAltText('Ваше фото')).toBeTruthy()
    expect(container.querySelector('input[type="file"]')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Удалить фото' })).toBeNull()
  })

  it('в редактировании фото заменяется и удаляется', async () => {
    const calls = mockApi()
    renderCard(true, true)
    expect(screen.getByText('Заменить фото')).toBeTruthy()

    expect(((await screen.findByAltText('Ваше фото')) as HTMLImageElement).src).toBe(
      'blob:photo',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Удалить фото' }))

    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({ url: '/api/resumes/photo', method: 'DELETE' }),
      ),
    )
  })
})
