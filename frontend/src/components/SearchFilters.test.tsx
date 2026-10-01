/** Слайдер зарплаты и меню «Ещё».
 *
 * Что здесь закреплено: левый край слайдера — «без порога», а не «от 0 ₽»;
 * порог выше шкалы, набранный руками, не обрезается слайдером; включённое
 * условие в свёрнутом меню видно по счётчику, а меню закрывается кликом мимо
 * и по Escape — иначе спрятанный фильтр сужал бы поиск незаметно.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FilterMenu } from './FilterMenu'
import { SalaryInput } from './SalaryInput'
import { formatSalary } from './ui'

function renderSalary(value: number | null) {
  const onChange = vi.fn()
  render(
    <SalaryInput
      value={value}
      anySalary
      onChange={onChange}
      onAnySalaryChange={() => {}}
    />,
  )
  return { onChange, slider: screen.getByRole('slider', { name: 'Порог зарплаты' }) }
}

describe('SalaryInput slider', () => {
  it('sends the chosen threshold', () => {
    const { onChange, slider } = renderSalary(null)
    fireEvent.change(slider, { target: { value: '230000' } })
    expect(onChange).toHaveBeenCalledWith(230_000)
  })

  it('treats the left end as no threshold', () => {
    const { onChange, slider } = renderSalary(200_000)
    fireEvent.change(slider, { target: { value: '0' } })
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it('keeps a typed value above the scale', () => {
    const { slider } = renderSalary(800_000)
    expect((slider as HTMLInputElement).value).toBe('500000')
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe(
      formatSalary(800_000),
    )
  })
})

describe('FilterMenu', () => {
  function renderMenu(active: number) {
    render(
      <>
        <button>снаружи</button>
        <FilterMenu label="Ещё" active={active}>
          <label>
            <input type="checkbox" /> опция
          </label>
        </FilterMenu>
      </>,
    )
    return screen.getByText('Ещё').closest('details')!
  }

  it('shows how many hidden options are on', () => {
    renderMenu(2)
    expect(screen.getByText('2').textContent).toBe('2')
  })

  it('closes on a click outside and on Escape', () => {
    const menu = renderMenu(0)
    menu.open = true
    fireEvent.pointerDown(screen.getByText('снаружи'))
    expect(menu.open).toBe(false)

    menu.open = true
    fireEvent.pointerDown(screen.getByText('опция'))
    expect(menu.open).toBe(true)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(menu.open).toBe(false)
  })
})
