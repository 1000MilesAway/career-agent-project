/** A CV list edited as tags: the chosen ones as chips, a field to type in,
 *  and popular tags matching what is typed in a list under it.
 *
 *  The tags sit apart from the field, so the field reads as a search and not
 *  as text: Backspace there erases what is typed and never a tag, which go
 *  only by their ×. The list only suggests: Enter on text that matches
 *  nothing adds it as typed, and so does a comma. Repeats are dropped
 *  regardless of case: "python" next to "Python" is one skill shown twice. */

import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Highlight, fold, matchCities } from './CitySelect'
import './city.css'
import './tags.css'

export function TagInput({
  label,
  value,
  onChange,
  suggestions,
}: {
  label: string
  value: string[]
  onChange: (value: string[]) => void
  suggestions: readonly string[]
}) {
  const id = useId()
  const listRef = useRef<HTMLUListElement>(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const chosen = new Set(value.map(fold))
  const typed = query.trim()
  const matches = matchCities(
    suggestions.filter((tag) => !chosen.has(fold(tag))),
    query,
  )
  // What was typed, as its own last option, unless it is already a tag or
  // spelled exactly like a suggestion (then that suggestion is the option).
  const custom =
    typed && !chosen.has(fold(typed)) && !suggestions.some((t) => fold(t) === fold(typed))
      ? typed
      : null
  const options = custom ? [...matches, custom] : matches

  useEffect(() => {
    // Optional call: jsdom has no scrollIntoView.
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active, open])

  function add(tag: string) {
    const clean = tag.trim()
    if (clean && !chosen.has(fold(clean))) onChange([...value, clean])
    setQuery('')
    setActive(0)
  }

  function remove(index: number) {
    onChange(value.filter((_, i) => i !== index))
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) return setOpen(true)
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((current) => Math.min(Math.max(current + step, 0), options.length - 1))
    } else if (event.key === 'Enter') {
      // Never the form's submit: Enter here means "this tag".
      event.preventDefault()
      if (open && options[active]) add(options[active])
      else if (typed) add(typed)
    } else if (event.key === ',') {
      event.preventDefault()
      if (typed) add(typed)
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div className="editor__field">
      <span className="eyebrow" id={`${id}-label`}>
        {label}
      </span>
      {value.length ? (
        <div className="chips tags__chips">
          {value.map((tag, index) => (
            <span key={tag} className="chip tags__chip">
              {tag}
              <button
                type="button"
                className="tags__remove"
                aria-label={`Убрать «${tag}»`}
                onClick={() => remove(index)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className={`city${open ? ' city--open' : ''}`}>
        <svg className="city__icon" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="7" cy="7" r="4.5" />
          <path d="M10.5 10.5 14 14" />
        </svg>
        <input
          className="input city__input"
          type="text"
          role="combobox"
          aria-labelledby={`${id}-label`}
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={open && options[active] ? `${id}-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder="Найти или добавить своё"
          value={query}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onBlur={() => {
            // A search left half-typed is dropped, as in the city field: only
            // a picked option or Enter makes a tag.
            setQuery('')
            setOpen(false)
          }}
          onKeyDown={onKeyDown}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
            setOpen(true)
          }}
        />
        {open && options.length ? (
          // mousedown is cancelled so the field keeps focus and its blur does
          // not close the list before the click lands.
          <ul
            ref={listRef}
            id={`${id}-list`}
            role="listbox"
            className="city__list"
            onMouseDown={(event) => event.preventDefault()}
          >
            {options.map((tag, index) => (
              <li
                key={tag}
                id={`${id}-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === active}
                className="city__option"
                onMouseEnter={() => setActive(index)}
                onClick={() => add(tag)}
              >
                {tag === custom ? (
                  <>
                    Добавить «<b>{tag}</b>»
                  </>
                ) : (
                  <Highlight text={tag} query={query} />
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  )
}
