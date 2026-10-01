/** Желаемая зарплата.
 *
 * Typed as digits, shown with thousands separators, sent as a number. A bare
 * <input type=number> gave no unit, no grouping and no idea what the number
 * would do, so people typed "250" meaning thousands and got an empty result
 * they could not explain.
 */

import { useRef, type ChangeEvent, type CSSProperties } from 'react'
import { formatSalary } from './ui'
import './salary.css'

/** The slider covers where nearly every threshold lands; a higher one is
 *  typed into the field, and the thumb then rests at the right end. */
const SLIDER_MAX = 500_000
const SLIDER_STEP = 10_000
const TICKS = [0, 100_000, 200_000, 300_000, 400_000, 500_000]

export function SalaryInput({
  value,
  anySalary,
  onChange,
  onAnySalaryChange,
}: {
  value: number | null
  anySalary: boolean
  onChange: (value: number | null) => void
  onAnySalaryChange: (anySalary: boolean) => void
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

      <div className="salary__slider">
        <input
          type="range"
          min={0}
          max={SLIDER_MAX}
          step={SLIDER_STEP}
          aria-label="Порог зарплаты"
          aria-valuetext={value ? `от ${formatSalary(value)} ₽` : 'без порога'}
          value={Math.min(value ?? 0, SLIDER_MAX)}
          // The filled part of the track is painted from this, since only
          // Firefox has a pseudo-element for it.
          style={
            {
              '--fill': `${(Math.min(value ?? 0, SLIDER_MAX) / SLIDER_MAX) * 100}%`,
            } as CSSProperties
          }
          // The left end is "no threshold", not "from 0 ₽": the same request,
          // but the hint below then says plainly that nothing is cut.
          onChange={(event) => onChange(Number(event.target.value) || null)}
        />
        <div className="salary__ticks" aria-hidden="true">
          {TICKS.map((tick) => (
            <span key={tick}>{tick === 0 ? 'любая' : `${tick / 1000}к`}</span>
          ))}
        </div>
      </div>

      <label
        className="toggle salary__any"
        title="Хабр отдаёт только вакансии с указанной вилкой, если задан порог. С галочкой берём и те, где зарплата не указана — их оценят бесплатные фильтры по заголовку."
      >
        <input
          type="checkbox"
          checked={anySalary}
          onChange={(event) => onAnySalaryChange(event.target.checked)}
        />
        и без указанной зарплаты
      </label>

      <p id="salary-hint" className="salary__hint muted">
        {hint}
      </p>
    </div>
  )
}
