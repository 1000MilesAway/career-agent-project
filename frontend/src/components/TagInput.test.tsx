/** Теги в резюме: подсказки из списка, своё значение и удаление. */

import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { TagInput } from './TagInput'

const SUGGESTIONS = ['Python', 'PostgreSQL', 'Go', 'Docker']

function Harness({ initial = ['Go'] }: { initial?: string[] }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <TagInput
        label="Навыки"
        value={value}
        suggestions={SUGGESTIONS}
        onChange={setValue}
      />
      <output>{value.join('|')}</output>
    </>
  )
}

const field = () => screen.getByRole('combobox', { name: 'Навыки' })
const value = () => screen.getByRole('status').textContent

describe('TagInput', () => {
  it('ищет по популярным и не предлагает уже выбранное', () => {
    render(<Harness />)
    fireEvent.focus(field())
    const all = screen.getAllByRole('option').map((o) => o.textContent)
    expect(all).toEqual(['Python', 'PostgreSQL', 'Docker'])

    fireEvent.change(field(), { target: { value: 'pos' } })
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'PostgreSQL',
      'Добавить «pos»',
    ])
  })

  it('Enter берёт подсказку, а не набранный обрывок', () => {
    render(<Harness />)
    fireEvent.change(field(), { target: { value: 'pyt' } })
    fireEvent.keyDown(field(), { key: 'Enter' })
    expect(value()).toBe('Go|Python')
  })

  it('своё значение добавляется, повтор без учёта регистра — нет', () => {
    render(<Harness />)
    fireEvent.change(field(), { target: { value: 'Rust' } })
    fireEvent.keyDown(field(), { key: 'Enter' })
    fireEvent.change(field(), { target: { value: 'go' } })
    fireEvent.keyDown(field(), { key: ',' })
    expect(value()).toBe('Go|Rust')
  })

  it('брошенный поиск не становится тегом', () => {
    render(<Harness />)
    fireEvent.change(field(), { target: { value: 'Kot' } })
    fireEvent.blur(field())
    expect(value()).toBe('Go')
    expect((field() as HTMLInputElement).value).toBe('')
  })

  it('тег убирается только крестиком, Backspace теги не трогает', () => {
    render(<Harness initial={['Go', 'Python', 'Docker']} />)
    fireEvent.keyDown(field(), { key: 'Backspace' })
    expect(value()).toBe('Go|Python|Docker')
    fireEvent.click(screen.getByRole('button', { name: 'Убрать «Python»' }))
    expect(value()).toBe('Go|Docker')
  })
})
