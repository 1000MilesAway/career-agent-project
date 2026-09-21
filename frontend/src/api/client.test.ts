import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, getToken, setToken } from './client'

function respond(status: number, body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  setToken(null)
})

describe('api', () => {
  it('добавляет Bearer к запросу, когда токен есть', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response('{}', { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    setToken('t0ken')

    await api('/api/state')

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer t0ken')
  })

  it('на 401 сбрасывает токен — иначе экран остаётся с мёртвыми кнопками', async () => {
    respond(401, { detail: 'нет' })
    setToken('stale')

    await expect(api('/api/state')).rejects.toThrow(ApiError)
    expect(getToken()).toBeNull()
  })

  // FastAPI кладёт сообщение в detail строкой, а для 422 — списком объектов.
  // Список, отрендеренный как есть, давал пользователю "[object Object]".
  it('разворачивает detail-список валидации в читаемый текст', async () => {
    respond(422, { detail: [{ msg: 'pages: слишком много' }, { msg: 'и ещё' }] })

    await expect(api('/api/vacancies/scan')).rejects.toThrow('pages: слишком много; и ещё')
  })

  it('объясняет 402 по-русски, а не кодом', async () => {
    respond(402, {})

    await expect(api('/api/evaluations/x')).rejects.toThrow(/Бюджет исчерпан/)
  })

  it('несёт status в ошибке, чтобы вызывающий мог различить случаи', async () => {
    respond(409, { detail: 'Сначала оцените вакансию' })

    await expect(api('/api/artifacts/x/letter')).rejects.toMatchObject({
      status: 409,
      message: 'Сначала оцените вакансию',
    })
  })
})
