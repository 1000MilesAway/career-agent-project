/** Профиль: как кабинет называет пользователя и как сохраняется имя.
 *
 * Имя необязательно — учётку заводит админ по одной почте, и пока имени нет,
 * в боковой панели должна стоять почта, а не пустая строка.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  updateProfile: vi.fn(() => Promise.resolve({})),
  deleteAccount: vi.fn(() => Promise.resolve(null)),
  logout: vi.fn(),
  mutate: vi.fn(() => Promise.resolve()),
}))

vi.mock('../api/hooks', () => ({
  STATE_KEY: '/api/state',
  updateProfile: mocks.updateProfile,
  deleteAccount: mocks.deleteAccount,
}))
vi.mock('swr', () => ({ useSWRConfig: () => ({ mutate: mocks.mutate }) }))
vi.mock('../auth', () => ({ useAuth: () => ({ logout: mocks.logout }) }))
vi.mock('./toast', () => ({ useToast: () => ({ info: vi.fn(), error: vi.fn() }) }))
vi.mock('./TelegramLogin', () => ({
  TelegramLink: ({
    linkedAs,
    canUnlink,
  }: {
    linkedAs: string | null
    canUnlink: boolean
  }) => (
    <p>
      tg:{String(linkedAs)}
      {canUnlink ? '' : ':only'}
    </p>
  ),
}))

const { ProfileDialog, displayName } = await import('./ProfileDialog')

const user: {
  email: string | null
  name: string | null
  consent_at: string | null
  telegram_name: string | null
} = {
  email: 'max@example.com',
  name: null,
  consent_at: null,
  telegram_name: '@max',
}

beforeAll(() => {
  // jsdom не реализует showModal, а компонент его вызывает.
  HTMLDialogElement.prototype.showModal = vi.fn()
  HTMLDialogElement.prototype.close = vi.fn()
})

describe('displayName', () => {
  it('без имени — почта', () => {
    expect(displayName(user)).toBe('max@example.com')
  })

  it('с именем — имя', () => {
    expect(displayName({ ...user, name: 'Макс' })).toBe('Макс')
  })

  it('без почты и имени — Telegram', () => {
    expect(displayName({ ...user, email: null })).toBe('@max')
  })
})

describe('ProfileDialog', () => {
  it('Telegram показывается в профиле', () => {
    render(<ProfileDialog open user={user} onClose={vi.fn()} />)
    expect(screen.getByText('tg:@max')).toBeTruthy()
  })

  it('учётка из Telegram: без строки почты, отвязать нельзя', () => {
    render(<ProfileDialog open user={{ ...user, email: null }} onClose={vi.fn()} />)
    expect(screen.queryByText('Почта для входа')).toBeNull()
    expect(screen.getByText('tg:@max:only')).toBeTruthy()
  })

  it('«Сохранить» неактивна, пока имя не изменено', () => {
    render(<ProfileDialog open user={{ ...user, name: 'Макс' }} onClose={vi.fn()} />)
    const save = screen.getByRole('button', {
      name: 'Сохранить',
      hidden: true,
    }) as HTMLButtonElement
    expect(save.disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('Имя'), { target: { value: '  Макс ' } })
    expect(save.disabled).toBe(true)
  })

  it('новое имя уходит на сервер, и состояние перечитывается', async () => {
    render(<ProfileDialog open user={user} onClose={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Имя'), { target: { value: 'Макс' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить', hidden: true }))

    await waitFor(() => expect(mocks.mutate).toHaveBeenCalledWith('/api/state'))
    expect(mocks.updateProfile).toHaveBeenCalledWith({ name: 'Макс' })
  })

  it('удаление аккаунта — только со второго нажатия, потом выход', async () => {
    render(<ProfileDialog open user={user} onClose={vi.fn()} />)
    fireEvent.click(screen.getByText('Удалить аккаунт', { selector: 'button' }))
    expect(mocks.deleteAccount).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('Да, удалить'))
    await waitFor(() => expect(mocks.logout).toHaveBeenCalled())
    expect(mocks.deleteAccount).toHaveBeenCalledOnce()
  })
})
