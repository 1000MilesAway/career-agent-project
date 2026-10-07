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
  loginTelegram: vi.fn(),
  telegram: null as { client_id: number } | null,
}))

vi.mock('swr', () => ({
  default: () => ({ data: { enabled: mocks.signupEnabled } }),
}))

vi.mock('../api/client', () => ({
  api: mocks.api,
  fetcher: vi.fn(),
  ApiError: class ApiError extends Error {
    readonly status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  },
}))

vi.mock('../auth', () => ({
  useAuth: () => ({
    login: vi.fn(),
    loginTelegram: mocks.loginTelegram,
    verifyEmail: mocks.verifyEmail,
    resetPassword: mocks.resetPassword,
  }),
}))

// The popup is Telegram's; the stand-in hands over a token on click.
vi.mock('../components/TelegramLogin', () => ({
  useTelegramConfig: () => mocks.telegram,
  TelegramButton: ({ onToken }: { onToken: (token: string) => void }) => (
    <button type="button" onClick={() => onToken('tg-token')}>
      Telegram
    </button>
  ),
}))

const { LoginPage } = await import('./LoginPage')
const { VerifyPage } = await import('./VerifyPage')
const { ResetPasswordPage } = await import('./ResetPasswordPage')
const { ApiError } = await import('../api/client')

beforeEach(() => {
  mocks.signupEnabled = true
  mocks.api.mockReset()
  mocks.verifyEmail.mockReset()
  mocks.resetPassword.mockReset()
  mocks.loginTelegram.mockReset()
  mocks.telegram = null
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
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Зарегистрироваться' }))

    await screen.findByText('Проверьте почту')
    expect(screen.getByText('new@example.com')).toBeTruthy()
    expect(mocks.api).toHaveBeenCalledWith('/api/auth/signup', {
      method: 'POST',
      json: { email: 'New@Example.com', password: 'long enough', consent: true },
    })
  })

  it('согласие на ПД — отдельная галочка, по умолчанию снята и обязательна', () => {
    renderLogin()
    expect(screen.queryByRole('checkbox')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Зарегистрироваться' }))
    const box = screen.getByRole('checkbox') as HTMLInputElement
    expect(box.checked).toBe(false)
    expect(box.required).toBe(true)
  })

  it('новичок из Telegram без галочки попадает на шаг согласия, тот же токен уходит снова', async () => {
    mocks.telegram = { client_id: 1 }
    mocks.loginTelegram
      .mockRejectedValueOnce(new ApiError('нужно согласие', 409))
      .mockResolvedValueOnce(undefined)
    renderLogin()

    fireEvent.click(screen.getByRole('button', { name: 'Telegram' }))
    await screen.findByText('Новая учётка')
    expect(mocks.loginTelegram).toHaveBeenLastCalledWith('tg-token', false)

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Создать учётку' }))
    await waitFor(() =>
      expect(mocks.loginTelegram).toHaveBeenLastCalledWith('tg-token', true),
    )
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
