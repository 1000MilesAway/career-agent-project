/** «Добавить»: ссылка показывается, потом добавляется; текст отвергается без запроса. */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  previewVacancy: vi.fn(),
  addVacancy: vi.fn(),
  mutate: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  previewVacancy: mocks.previewVacancy,
  addVacancy: mocks.addVacancy,
}))

vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))

vi.mock('./toast', () => ({
  useToast: () => ({ info: mocks.info, error: mocks.error }),
}))

const { AddVacancyDialog } = await import('./AddVacancyDialog')
const { ApiError } = await import('../api/client')

const LINK = 'https://career.habr.com/vacancies/1000'
const PREVIEW = {
  posting_id: 'p1',
  title: 'Аналитик данных',
  company: 'Ромашка',
  salary_text: 'от 200 000 ₽',
  location: null,
  url: LINK,
  closed_reason: null,
  saved: false,
}

// jsdom never opens a <dialog>, so its content counts as hidden to role queries.
const field = () => screen.getByPlaceholderText('Вставьте ссылку на вакансию')

async function paste(text: string) {
  await act(async () => {
    fireEvent.paste(field(), { clipboardData: { getData: () => text } })
  })
}

describe('AddVacancyDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('refuses text without asking the server', async () => {
    render(<AddVacancyDialog onClose={vi.fn()} />)
    await paste('Аналитик данных, удалённо')
    expect(screen.getByRole('alert', { hidden: true }).textContent).toBe(
      'Принимаем ссылки только с проверенных ресурсов',
    )
    expect(mocks.previewVacancy).not.toHaveBeenCalled()
  })

  it('shows a pasted link, then adds the posting it showed', async () => {
    mocks.previewVacancy.mockResolvedValue(PREVIEW)
    mocks.addVacancy.mockResolvedValue({ vacancy: { id: 'v1' }, created: true })
    const onClose = vi.fn()
    render(<AddVacancyDialog onClose={onClose} />)

    await paste(`  ${LINK}\n`)
    expect(mocks.previewVacancy).toHaveBeenCalledWith(LINK)
    expect(screen.getByText('Аналитик данных')).toBeTruthy()
    expect(screen.getByText('Ромашка · от 200 000 ₽')).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Добавить', hidden: true }))
    })
    expect(mocks.addVacancy).toHaveBeenCalledWith('p1')
    expect(mocks.mutate).toHaveBeenCalledWith('/api/state')
    expect(onClose).toHaveBeenCalled()
  })

  it("says the server's reason for a link it refused", async () => {
    mocks.previewVacancy.mockRejectedValue(
      new ApiError('Принимаем ссылки только с проверенных ресурсов.', 422),
    )
    render(<AddVacancyDialog onClose={vi.fn()} />)
    await paste('https://example.com/jobs/1')
    expect(screen.getByRole('alert', { hidden: true }).textContent).toBe(
      'Принимаем ссылки только с проверенных ресурсов.',
    )
    expect(mocks.addVacancy).not.toHaveBeenCalled()
  })

  it('hides a raw server failure behind a retry', async () => {
    mocks.previewVacancy.mockRejectedValue(new ApiError('Not Found', 404))
    render(<AddVacancyDialog onClose={vi.fn()} />)
    await paste(LINK)
    expect(screen.getByRole('alert', { hidden: true }).textContent).toBe(
      'Не получилось загрузить вакансию. Попробуйте ещё раз.',
    )
    const again = screen.getByRole('button', { name: 'Найти', hidden: true })
    expect((again as HTMLButtonElement).disabled).toBe(false)
  })

  it('does not offer to add what is already saved', async () => {
    mocks.previewVacancy.mockResolvedValue({ ...PREVIEW, saved: true })
    render(<AddVacancyDialog onClose={vi.fn()} />)
    await paste(LINK)
    const button = screen.getByRole('button', {
      name: 'Уже в избранном',
      hidden: true,
    }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('drops the preview once the link is edited', async () => {
    mocks.previewVacancy.mockResolvedValue(PREVIEW)
    render(<AddVacancyDialog onClose={vi.fn()} />)
    await paste(LINK)
    fireEvent.change(field(), { target: { value: `${LINK}1` } })
    expect(screen.queryByText('Аналитик данных')).toBeNull()
    expect(screen.getByRole('button', { name: 'Найти', hidden: true })).toBeTruthy()
  })
})
