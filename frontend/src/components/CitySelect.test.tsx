import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CitySelect } from './CitySelect'
import { CITIES } from './cities'

const options = (container: HTMLElement) =>
  [...container.querySelectorAll('option')].map((option) => option.textContent)

describe('CitySelect', () => {
  it('столицы первыми, дальше по алфавиту', () => {
    expect(CITIES.slice(0, 3)).toEqual(['Москва', 'Санкт-Петербург', 'Астрахань'])
    expect(CITIES.at(-1)).toBe('Ярославль')
  })

  it('город не из списка остаётся выбранным, а не стирается', () => {
    const { container } = render(
      <CitySelect value="Подольск" emptyLabel="не указан" onChange={() => {}} />,
    )
    expect(options(container).slice(0, 3)).toEqual(['не указан', 'Подольск', 'Москва'])
    expect(container.querySelector('select')?.value).toBe('Подольск')
  })

  it('выбор отдаёт название города, пустой вариант — пустую строку', () => {
    const onChange = vi.fn()
    const { container } = render(
      <CitySelect value="" emptyLabel="Любой" onChange={onChange} />,
    )
    const select = container.querySelector('select')!
    fireEvent.change(select, { target: { value: 'Казань' } })
    expect(onChange).toHaveBeenLastCalledWith('Казань')
    expect(options(container)).not.toContain('Подольск')
  })
})
