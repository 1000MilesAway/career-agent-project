/** Источники: разделы и поиск из адреса, переключатель скрывает источник,
 * переименование сохраняется по Enter и не уходит, если ничего не менялось.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AdminSources } from '../api/types'

const mocks = vi.hoisted(() => ({
  setSourceHidden: vi.fn(() => Promise.resolve()),
  setCompanyName: vi.fn(() => Promise.resolve()),
}))

const stats = { postings: 10, active: 4, last_seen_at: null }

const data: AdminSources = {
  aggregators: [{ source: 'habr', label: 'Хабр Карьера', fetched_at: null, ...stats }],
  boards: [
    {
      key: 'sber/sber',
      label: 'Сбер',
      group: 'ru',
      hidden: false,
      ...stats,
      names: [
        { name: 'ПАО Сбербанк', display_name: null, ...stats },
        { name: 'АО "СберТех"', display_name: 'СберТех', ...stats },
      ],
    },
    {
      key: 'yandex/yandex',
      label: 'Яндекс',
      group: 'ru',
      hidden: true,
      ...stats,
      names: [],
    },
  ],
  channels: [
    {
      key: 'pydevjob',
      name: '@pydevjob',
      display_name: null,
      group: 'core',
      hidden: false,
      ...stats,
    },
  ],
}

vi.mock('../api/hooks', () => ({
  useAdminSources: () => ({ data, error: null, isLoading: false }),
  setSourceHidden: mocks.setSourceHidden,
  setCompanyName: mocks.setCompanyName,
}))

vi.mock('../components/toast', () => ({
  useToast: () => ({ info: vi.fn(), error: vi.fn() }),
}))

const { AdminSourcesPage } = await import('./AdminSourcesPage')

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AdminSourcesPage />
    </MemoryRouter>,
  )

beforeEach(() => {
  mocks.setSourceHidden.mockClear()
  mocks.setCompanyName.mockClear()
})

describe('AdminSourcesPage', () => {
  it('opens on the aggregators', () => {
    at('/admin/sources')
    expect(screen.getByText('Хабр Карьера')).toBeTruthy()
    expect(screen.queryByText('Сбер')).toBeNull()
  })

  it('finds a board by one of its company names', () => {
    at('/admin/sources?tab=boards&q=сбертех')
    expect(screen.getByText('Сбер')).toBeTruthy()
    expect(screen.queryByText('Яндекс')).toBeNull()
  })

  it('the switch hides a board and brings a hidden one back', () => {
    at('/admin/sources?tab=boards')
    fireEvent.click(screen.getByLabelText('Показывать пользователям: Сбер'))
    expect(mocks.setSourceHidden).toHaveBeenLastCalledWith('company', 'sber/sber', true)
    fireEvent.click(screen.getByLabelText('Показывать пользователям: Яндекс'))
    expect(mocks.setSourceHidden).toHaveBeenLastCalledWith(
      'company',
      'yandex/yandex',
      false,
    )
  })

  it('renames a company on Enter, and not when nothing changed', () => {
    at('/admin/sources?tab=boards')
    const field = screen.getByLabelText('Название для пользователей: ПАО Сбербанк')
    fireEvent.blur(field)
    expect(mocks.setCompanyName).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: 'Сбербанк' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    fireEvent.blur(field)
    expect(mocks.setCompanyName).toHaveBeenCalledWith('ПАО Сбербанк', 'Сбербанк')
  })

  it('a channel is switched by its name without "@"', () => {
    at('/admin/sources?tab=channels')
    expect(screen.getByText('основные')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Показывать пользователям: @pydevjob'))
    expect(mocks.setSourceHidden).toHaveBeenLastCalledWith('telegram', 'pydevjob', true)
  })
})
