/** Закрытая панель не должна быть видна.
 *
 * Дефект, который это ловит: `display: flex` стоял на `.drawer`, а не на
 * `.drawer[open]`, и перебивал встроенное `display: none` браузера — пустая
 * панель с заголовком и кнопкой «Закрыть» висела внизу каждой страницы.
 *
 * jsdom не считает каскад, поэтому проверяется сам CSS: правила, задающего
 * `display` в обход состояния `[open]`, в файле быть не должно.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Drawer } from './drawer'

// Относительно корня проекта: под Vite `import.meta.url` не file://-адрес,
// и readFileSync такой URL не принимает.
const css = readFileSync(resolve('src/components/drawer.css'), 'utf8')

/** Тело правила по точному селектору — то, что между `{` и `}`.
 *
 * Разбор поиском подстроки, а не регуляркой: селекторы здесь содержат `.` и
 * `[]`, и экранирование их в шаблоне — лишний источник ошибок в самом тесте. */
function ruleBody(selector: string): string {
  for (const chunk of css.split('}')) {
    const brace = chunk.indexOf('{')
    if (brace === -1) continue
    // Последняя строка перед `{` — сам селектор; выше могут быть комментарии.
    const head = chunk.slice(0, brace).trim().split('\n').pop()?.trim()
    if (head === selector) return chunk.slice(brace + 1)
  }
  return ''
}

describe('drawer.css', () => {
  it('.drawer не задаёт display — иначе закрытый dialog остаётся на странице', () => {
    const body = ruleBody('.drawer')
    expect(body).not.toBe('')
    expect(body).not.toMatch(/(^|;)\s*display\s*:/)
  })

  it('.drawer[open] задаёт display: flex', () => {
    expect(ruleBody('.drawer[open]')).toMatch(/display\s*:\s*flex/)
  })
})

describe('Drawer', () => {
  it('закрытая панель не несёт атрибута open', () => {
    // jsdom не реализует showModal, а компонент его вызывает.
    HTMLDialogElement.prototype.showModal = vi.fn()
    HTMLDialogElement.prototype.close = vi.fn()

    const { container } = render(
      <Drawer open={false} title="Отчёт" onClose={vi.fn()}>
        содержимое
      </Drawer>,
    )

    expect(container.querySelector('dialog')?.hasAttribute('open')).toBe(false)
  })
})
