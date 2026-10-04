/** A city picked from a list rather than typed: a typed "Мск" or "Питер "
 *  is one more spelling for the filter to know, a picked one is not. */

import { CITIES } from './cities'

export function CitySelect({
  value,
  onChange,
  emptyLabel,
}: {
  value: string
  onChange: (value: string) => void
  /** What the empty choice means here: "любой" in a search, "не указан" in a CV. */
  emptyLabel: string
}) {
  // A city the CV parser read that is not on the list stays choosable:
  // dropping it from the options would silently clear it on the next save.
  const extra = value && !CITIES.includes(value) ? value : null
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}</option>
      {extra ? <option value={extra}>{extra}</option> : null}
      {CITIES.map((city) => (
        <option key={city} value={city}>
          {city}
        </option>
      ))}
    </select>
  )
}
