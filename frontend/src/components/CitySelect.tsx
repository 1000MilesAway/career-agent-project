/** A city picked from a list rather than typed: a typed "Мск" or "Питер "
 *  is one more spelling for the filter to know, a picked one is not.
 *
 *  A combobox, not a <select>: the native list does jump to a typed prefix,
 *  but shows nothing of what was typed, so finding a city among forty looked
 *  like the list moving on its own. Text that matches no city is never
 *  committed — leaving the field restores the last picked value. */

import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { CITIES } from './cities'
import './city.css'

/** Case and ё folded: "орел" finds "Орёл", "спб" does not — no aliases. */
export const fold = (text: string) => text.trim().toLowerCase().replaceAll('ё', 'е')

/** Cities containing the query, those that start with it first. */
export function matchCities(cities: readonly string[], query: string): string[] {
  const q = fold(query)
  if (!q) return [...cities]
  const starts: string[] = []
  const contains: string[] = []
  for (const city of cities) {
    const at = fold(city).indexOf(q)
    if (at === 0) starts.push(city)
    else if (at > 0) contains.push(city)
  }
  return [...starts, ...contains]
}

export function Highlight({ text, query }: { text: string; query: string }) {
  const q = fold(query)
  const at = q ? fold(text).indexOf(q) : -1
  if (at < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, at + q.length)}</mark>
      {text.slice(at + q.length)}
    </>
  )
}

export function CitySelect({
  value,
  onChange,
  emptyLabel,
  id,
}: {
  value: string
  onChange: (value: string) => void
  /** What the empty choice means here: "любой" in a search, "не указан" in a CV. */
  emptyLabel: string
  id?: string
}) {
  const listId = useId()
  const listRef = useRef<HTMLUListElement>(null)
  const [query, setQuery] = useState<string | null>(null)
  const [active, setActive] = useState(0)

  // A city the CV parser read that is not on the list stays choosable:
  // dropping it from the options would silently clear it on the next save.
  const all = value && !CITIES.includes(value) ? [value, ...CITIES] : CITIES
  const open = query !== null
  const options = open ? matchCities(all, query) : []

  useEffect(() => {
    // Optional call: jsdom has no scrollIntoView.
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active, open])

  function start() {
    setQuery('')
    setActive(Math.max(0, all.indexOf(value)))
  }

  function pick(city: string) {
    setQuery(null)
    if (city !== value) onChange(city)
  }

  function leave() {
    if (query === null) return
    // An exact name typed in full counts as picked; anything else is dropped.
    const exact = all.find((city) => fold(city) === fold(query))
    if (exact) pick(exact)
    else setQuery(null)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) return start()
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((current) => Math.min(Math.max(current + step, 0), options.length - 1))
    } else if (event.key === 'Enter' && open) {
      event.preventDefault()
      if (options[active]) pick(options[active])
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setQuery(null)
    }
  }

  return (
    <div className={`city${open ? ' city--open' : ''}`}>
      <svg className="city__icon" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5 14 14" />
      </svg>
      <input
        id={id}
        className="input city__input"
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && options[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder={open && value ? value : emptyLabel}
        value={open ? query : value}
        onFocus={start}
        onClick={() => (open ? undefined : start())}
        onBlur={leave}
        onKeyDown={onKeyDown}
        onChange={(event) => {
          setQuery(event.target.value)
          setActive(0)
        }}
      />
      {value && !open ? (
        <button
          type="button"
          className="city__clear"
          aria-label="Сбросить город"
          onClick={() => onChange('')}
        >
          ×
        </button>
      ) : null}
      {open ? (
        // mousedown is cancelled so the input keeps focus and its blur does
        // not close the list before the click lands.
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className="city__list"
          onMouseDown={(event) => event.preventDefault()}
        >
          {options.map((city, index) => (
            <li
              key={city}
              id={`${listId}-${index}`}
              data-index={index}
              role="option"
              aria-selected={index === active}
              className={`city__option${city === value ? ' city__option--current' : ''}`}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(city)}
            >
              <Highlight text={city} query={query} />
            </li>
          ))}
          {!options.length ? (
            <li className="city__none">Такого города нет в списке</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}
