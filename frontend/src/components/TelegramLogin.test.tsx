/** Кнопка входа через Telegram: что уходит в Telegram и что возвращается.
 *
 * Если client_id или nonce не дойдут до Telegram, попап откажет или сервер
 * отвергнет токен — а пользователь увидит лишь «ничего не произошло».
 * Закрытый попап — это передумал, а не ошибка, и тост на него не нужен.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TelegramButton } from './TelegramLogin'

const config = { client_id: 8606493609, nonce: 'n-1' }

type Callback = (result: { id_token?: string; error?: string }) => void

function fakeTelegram(result: { id_token?: string; error?: string }) {
  const auth = vi.fn((_options: unknown, callback: Callback) => callback(result))
  window.Telegram = { Login: { auth } }
  return auth
}

function renderButton(onToken = vi.fn(), onError = vi.fn()) {
  render(
    <TelegramButton config={config} onToken={onToken} onError={onError}>
      Войти через Telegram
    </TelegramButton>,
  )
  fireEvent.click(screen.getByRole('button', { name: /Войти через Telegram/ }))
  return { onToken, onError }
}

afterEach(() => {
  delete window.Telegram
})

describe('TelegramButton', () => {
  it('просит Telegram о входе для нашего бота и с нашим nonce', () => {
    const auth = fakeTelegram({ id_token: 't' })
    renderButton()

    expect(auth).toHaveBeenCalledWith(
      { client_id: 8606493609, nonce: 'n-1', lang: 'ru' },
      expect.any(Function),
    )
  })

  it('id_token уходит обработчику как есть', () => {
    fakeTelegram({ id_token: 'eyJ.token' })
    const { onToken, onError } = renderButton()

    expect(onToken).toHaveBeenCalledWith('eyJ.token')
    expect(onError).not.toHaveBeenCalled()
  })

  it('закрытый попап — не ошибка', () => {
    fakeTelegram({ error: 'popup_closed' })
    const { onToken, onError } = renderButton()

    expect(onToken).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('отказ Telegram показывается, а не глотается', () => {
    fakeTelegram({ error: 'access_denied' })
    const { onError } = renderButton()

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('access_denied') }),
    )
  })

  it('пока скрипт не загрузился, клик объясняет, что ждать', () => {
    const { onError } = renderButton()

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('загружается') }),
    )
  })
})
