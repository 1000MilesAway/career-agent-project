/** Виджет Telegram зовёт страницу по имени глобальной функции из data-onauth.
 *
 * Если имя и функция разойдутся, кнопка нажимается, Telegram подтверждает
 * вход — и ничего не происходит. Проверяется ровно эта связка.
 */

import { cleanup, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TelegramButton } from './TelegramLogin'

function widgetScript(container: HTMLElement): HTMLScriptElement {
  const script = container.querySelector('script')
  if (!script) throw new Error('скрипт виджета не вставлен')
  return script
}

describe('TelegramButton', () => {
  it('рисует виджет для нужного бота', () => {
    const { container } = render(
      <TelegramButton bot="career_helper_bot" onAuth={vi.fn()} />,
    )
    const script = widgetScript(container)

    expect(script.src).toContain('telegram.org/js/telegram-widget.js')
    expect(script.dataset.telegramLogin).toBe('career_helper_bot')
  })

  it('ответ Telegram доходит до обработчика целиком', () => {
    const onAuth = vi.fn()
    const { container } = render(<TelegramButton bot="b" onAuth={onAuth} />)
    const name = widgetScript(container).dataset.onauth!.replace('(user)', '')

    const answer = { id: 42, auth_date: 1, hash: 'x' }
    ;(window as unknown as Record<string, (d: unknown) => void>)[name](answer)

    expect(onAuth).toHaveBeenCalledWith(answer)
  })

  it('после ухода со страницы глобальный обработчик убран', () => {
    const { container } = render(<TelegramButton bot="b" onAuth={vi.fn()} />)
    const name = widgetScript(container).dataset.onauth!.replace('(user)', '')
    cleanup()

    expect(name in window).toBe(false)
  })
})
