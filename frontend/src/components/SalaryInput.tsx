/** Желаемая зарплата.
 *
 * Typed as digits, shown with thousands separators, sent as a number. A bare
 * <input type=number> gave no unit, no grouping and no idea what the number
 * would do, so people typed "250" meaning thousands and got an empty result
 * they could not explain.
 */

import { useRef, type ChangeEvent } from 'react'
import { formatSalary } from './ui'
import './salary.css'

const PRESETS = [150_000, 200_000, 250_000, 300_000, 400_000]

export function SalaryInput({
  value,
  anySalary,
  onChange,
}: {
  value: number | null
  anySalary: boolean
  onChange: (value: number | null) => void
}) {
  const ref = useRef<HTMLInputElement>(null)

  function handleInput(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target
    // The caret would jump to the end on every keystroke once separators are
    // inserted, so it is restored by counting digits, not characters.
    const digitsBeforeCaret = (
      input.value.slice(0, input.selectionStart ?? 0).match(/\d/g) ?? []
    ).length
    const digits = input.value.replace(/\D/g, '').slice(0, 9)
    const next = digits ? Number(digits) : null
    const text = next === null ? '' : formatSalary(next)

    onChange(next)

    // Applied after React has written the controlled value, or the position
    // is computed against the old text.
    queueMicrotask(() => {
      const element = ref.current
      if (!element) return
      let seen = 0
      let position = 0
      for (; position < text.length && seen < digitsBeforeCaret; position++) {
        if (/\d/.test(text[position])) seen++
      }
      element.setSelectionRange(position, position)
    })
  }

  const hint = !value ? (
    'Без порога — покажем всё, что пройдёт фильтры по заголовку.'
  ) : anySalary ? (
    <>
      Отсеем всё, где вилка ниже <b>{formatSalary(value)} ₽</b>. Вакансии без указанной
      зарплаты останутся — их судим по заголовку.
    </>
  ) : (
    <>
      Ищем только от <b>{formatSalary(value)} ₽</b>. Вакансии без указанной зарплаты Хабр не
      отдаст — их не будет вовсе.
    </>
  )

  return (
    <div className="salary">
      <label className="salary__label" htmlFor="salary-input">
        Желаемая зарплата
      </label>
      <div className="salary__field">
        <input
          id="salary-input"
          ref={ref}
          type="text"
          inputMode="numeric"
          autoComplete="off"
          placeholder="например, 250 000"
          aria-describedby="salary-hint"
          value={value === null ? '' : formatSalary(value)}
          onChange={handleInput}
        />
        <span className="salary__unit">₽ в месяц</span>
      </div>

      <div className="salary__presets">
        {PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="chip chip--pick"
            aria-pressed={preset === value}
            // Pressing the active preset clears it: otherwise the only way
            // back to "no threshold" is to select the text and delete it.
            onClick={() => onChange(preset === value ? null : preset)}
          >
            {formatSalary(preset)}
          </button>
        ))}
      </div>

      <p id="salary-hint" className="salary__hint muted">
        {hint}
      </p>
    </div>
  )
}
