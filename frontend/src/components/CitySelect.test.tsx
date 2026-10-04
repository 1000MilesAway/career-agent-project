import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CitySelect, matchCities } from './CitySelect'
import { CITIES } from './cities'

function renderCity(value: string) {
  const onChange = vi.fn()
  render(<CitySelect value={value} emptyLabel="Любой" onChange={onChange} />)
  const input = screen.getByRole('combobox') as HTMLInputElement
  const options = () => screen.queryAllByRole('option').map((o) => o.textContent)
  return { onChange, input, options }
}

describe('CitySelect', () => {
  it('столицы первыми, дальше по алфавиту', () => {
    expect(CITIES.slice(0, 3)).toEqual(['Москва', 'Санкт-Петербург', 'Астрахань'])
    expect(CITIES.at(-1)).toBe('Ярославль')
  })

  it('набранное видно в поле, список сужается по нему', () => {
    const { input, options } = renderCity('')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'кра' } })
    expect(input.value).toBe('кра')
    expect(options()).toEqual(['Краснодар', 'Красноярск'])
  })

  it('начало названия выше совпадения в середине, ё не мешает', () => {
    expect(matchCities(['Москва', 'Самара', 'Махачкала'], 'ма')).toEqual([
      'Махачкала',
      'Самара',
    ])
    expect(matchCities(['Тольятти', 'Орёл'], 'орел')).toEqual(['Орёл'])
  })

  it('Enter выбирает подсвеченный город', () => {
    const { input, onChange } = renderCity('')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'каз' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith('Казань')
  })

  it('клик по варианту выбирает его', () => {
    const { input, onChange } = renderCity('')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'омск' } })
    fireEvent.click(screen.getByRole('option', { name: 'Омск' }))
    expect(onChange).toHaveBeenLastCalledWith('Омск')
  })

  it('недобранный текст при уходе из поля не сохраняется', () => {
    const { input, onChange } = renderCity('Москва')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'Мос' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('Москва')
  })

  it('без совпадений говорит об этом', () => {
    const { input, options } = renderCity('')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'Питер' } })
    expect(options()).toEqual([])
    expect(screen.getByText('Такого города нет в списке')).toBeTruthy()
  })

  it('крестик сбрасывает город в пустую строку', () => {
    const { onChange } = renderCity('Казань')
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить город' }))
    expect(onChange).toHaveBeenLastCalledWith('')
  })

  it('город не из списка остаётся выбранным, а не стирается', () => {
    const { input, options } = renderCity('Подольск')
    expect(input.value).toBe('Подольск')
    fireEvent.focus(input)
    expect(options().slice(0, 2)).toEqual(['Подольск', 'Москва'])
  })
})
