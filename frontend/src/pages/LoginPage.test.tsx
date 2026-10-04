/** Регистрация по почте: форма, «письмо ушло» и подтверждение по ссылке.
 *
 * Без настроенной почты регистрации на экране быть не должно — иначе человек
 * заполнит форму и получит 404. Подтверждение — только по нажатию: сканер
 * ссылок в почтовом ящике не должен подтверждать адрес за владельца.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  signupEnabled: true,
  api: vi.fn(),
  verifyEmail: vi.fn(),
  resetPassword: vi.fn(),
}))

vi.mock('swr', () => ({
  default: () => ({ data: { enabled: mocks.signupEnabled } }),
}))

vi.mock('../api/client', () => ({ api: mocks.api, fetcher: vi.fn() }))

vi.mock('../auth', () => ({
  useAuth: () => ({
    login: vi.fn(),
    loginTelegram: vi.fn(),
    verifyEmail: mocks.verifyEmail,
    resetPassword: mocks.resetPassword,
  }),
}))

vi.mock('../components/TelegramLogin', () => ({
  useTelegramConfig: () => null,
  TelegramButton: () => null,
}))

const { LoginPage } = await import('./LoginPage')
const { VerifyPage } = await import('./VerifyPage')
const { ResetPasswordPage } = await import('./ResetPasswordPage')

beforeEach(() => {
  mocks.signupEnabled = true
  mocks.api.mockReset()
  mocks.verifyEmail.mockReset()
  mocks.resetPassword.mockReset()
})

describe('LoginPage', () => {
  it('без почты на сервере регистрации нет', () => {
    mocks.signupEnabled = false
    renderLogin()
    expect(screen.queryByRole('button', { name: 'Зарегистрироваться' })).toBeNull()
  })

  it('регистрация отправляет почту и пароль и говорит, куда ушло письмо', async () => {
    mocks.api.mockResolvedValue({ email: 'new@example.com' })
    const { container } = renderLogin()

    fireEvent.click(screen.getByRole('button', { name: 'Зарегистрироваться' }))
    fireEvent.change(container.querySelector('input[type=email]')!, {
      target: { value: ' New@Example.com ' },
    })
    fireEvent.change(container.querySelector('input[type=password]')!, {
      target: { value: 'long enough' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Зарегистрироваться' }))

    await screen.findByText('Проверьте почту')
    expect(screen.getByText('new@example.com')).toBeTruthy()
    expect(mocks.api).toHaveBeenCalledWith('/api/auth/signup', {
      method: 'POST',
      json: { email: 'New@Example.com', password: 'long enough' },
    })
  })
})

describe('сброс пароля', () => {
  it('без почты на сервере ссылки «Забыли пароль?» нет', () => {
    mocks.signupEnabled = false
    renderLogin()
    expect(screen.queryByRole('button', { name: 'Забыли пароль?' })).toBeNull()
  })

  it('запрос уходит с почтой и не говорит, есть ли такая учётка', async () => {
    mocks.api.mockResolvedValue({ email: 'me@example.com' })
    const { container } = renderLogin()

    fireEvent.click(screen.getByRole('button', { name: 'Забыли пароль?' }))
    expect(container.querySelector('input[type=password]')).toBeNull()
    fireEvent.change(container.querySelector('input[type=email]')!, {
      target: { value: ' me@example.com ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Прислать ссылку' }))

    await screen.findByText(/Если учётка с адресом/)
    expect(mocks.api).toHaveBeenCalledWith('/api/auth/password-reset', {
      method: 'POST',
      json: { email: 'me@example.com' },
    })
  })
})

function renderReset(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/" element={<p>кабинет</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ResetPasswordPage', () => {
  it('сохраняет новый пароль по токену из ссылки и открывает кабинет', async () => {
    mocks.resetPassword.mockResolvedValue(undefined)
    const { container } = renderReset('/reset-password?token=abc')
    expect(mocks.resetPassword).not.toHaveBeenCalled()

    fireEvent.change(container.querySelector('input[type=password]')!, {
      target: { value: 'brand new one' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить и войти' }))
    await screen.findByText('кабинет')
    expect(mocks.resetPassword).toHaveBeenCalledWith('abc', 'brand new one')
  })

  it('отказ сервера показывается, а не глотается', async () => {
    mocks.resetPassword.mockRejectedValue(new Error('Ссылка устарела или уже использована'))
    const { container } = renderReset('/reset-password?token=old')
    fireEvent.change(container.querySelector('input[type=password]')!, {
      target: { value: 'brand new one' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить и войти' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/устарела/))
  })

  it('без токена в ссылке формы нет', () => {
    const { container } = renderReset('/reset-password')
    expect(container.querySelector('input[type=password]')).toBeNull()
  })
})

function renderVerify(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/verify" element={<VerifyPage />} />
        <Route path="/" element={<p>кабинет</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('VerifyPage', () => {
  it('ничего не подтверждает, пока не нажали кнопку', async () => {
    mocks.verifyEmail.mockResolvedValue(undefined)
    renderVerify('/verify?token=abc')
    expect(mocks.verifyEmail).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить почту' }))
    await screen.findByText('кабинет')
    expect(mocks.verifyEmail).toHaveBeenCalledWith('abc')
  })

  it('отказ сервера показывается, а не глотается', async () => {
    mocks.verifyEmail.mockRejectedValue(new Error('Ссылка устарела'))
    renderVerify('/verify?token=old')
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить почту' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/устарела/))
  })

  it('без токена в ссылке кнопки нет', () => {
    renderVerify('/verify')
    expect(screen.queryByRole('button', { name: 'Подтвердить почту' })).toBeNull()
  })
})

// The page links to the documents with router links, so it needs a router.
function renderLogin() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  )
}
