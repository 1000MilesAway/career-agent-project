import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Select } from './Select'

const OPTIONS = [
  { value: 'none', label: 'Не решено' },
  { value: 'applied', label: 'Откликнулся' },
  { value: 'offer', label: 'Оффер' },
]

function renderSelect(value = 'none') {
  const onChange = vi.fn()
  render(<Select value={value} options={OPTIONS} onChange={onChange} label="Статус" />)
  const trigger = screen.getByRole('combobox', { name: 'Статус' })
  return { onChange, trigger }
}

describe('Select', () => {
  it('показывает выбранное и открывает список по клику', () => {
    const { trigger } = renderSelect('applied')
    expect(trigger.textContent).toBe('Откликнулся')
    expect(screen.queryByRole('listbox')).toBeNull()
    fireEvent.click(trigger)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Не решено',
      'Откликнулся',
      'Оффер',
    ])
    expect(screen.getByRole('option', { selected: true }).textContent).toBe('Откликнулся')
  })

  it('выбор пункта закрывает список и сообщает значение', () => {
    const { onChange, trigger } = renderSelect()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: 'Оффер' }))
    expect(onChange).toHaveBeenCalledWith('offer')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('стрелки и Enter выбирают с клавиатуры, Esc закрывает без выбора', () => {
    const { onChange, trigger } = renderSelect()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('applied')

    onChange.mockClear()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    fireEvent.keyDown(trigger, { key: 'End' })
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('буква на закрытом списке переключает на пункт с неё', () => {
    const { onChange, trigger } = renderSelect()
    fireEvent.keyDown(trigger, { key: 'о' })
    expect(onChange).toHaveBeenCalledWith('applied')
  })

  it('клик мимо закрывает список', () => {
    const { trigger } = renderSelect()
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})
