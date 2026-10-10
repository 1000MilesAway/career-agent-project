/** A CV list edited as tags: the chosen ones as chips, a field to type in,
 *  and popular tags matching what is typed in a list under it.
 *
 *  The list only suggests: Enter on text that matches nothing adds it as
 *  typed, and so does a comma. Repeats are dropped regardless of case, since
 *  "python" next to "Python" is one skill shown twice. */

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
  const inputRef = useRef<HTMLInputElement>(null)
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
    } else if (event.key === 'Backspace' && !query && value.length) {
      remove(value.length - 1)
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
      <div className={`city tags${open ? ' city--open tags--open' : ''}`}>
        {/* A click on the box's empty part goes to the field, as in an input. */}
        <div
          className="input tags__box"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              event.preventDefault()
              inputRef.current?.focus()
            }
          }}
        >
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
          <input
            ref={inputRef}
            className="tags__input"
            type="text"
            role="combobox"
            aria-labelledby={`${id}-label`}
            aria-expanded={open}
            aria-controls={`${id}-list`}
            aria-autocomplete="list"
            aria-activedescendant={open && options[active] ? `${id}-${active}` : undefined}
            autoComplete="off"
            spellCheck={false}
            placeholder={value.length ? 'Ещё…' : 'Найти или добавить'}
            value={query}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onBlur={() => {
              // Typed and left is still meant: it is kept, not lost.
              if (typed) add(typed)
              setOpen(false)
            }}
            onKeyDown={onKeyDown}
            onChange={(event) => {
              setQuery(event.target.value)
              setActive(0)
              setOpen(true)
            }}
          />
        </div>
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
