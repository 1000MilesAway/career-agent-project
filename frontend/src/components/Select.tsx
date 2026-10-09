/** A dropdown drawn by the page, not the OS: the native <select> list wears
 *  the system's light theme and font whatever the cabinet looks like.
 *
 *  A button that opens a listbox (the ARIA "select-only combobox" pattern):
 *  arrows, Home/End, Enter/Space and Esc work as on the native control, and
 *  a typed letter jumps to the first option starting with it. */

import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import './select.css'

export interface SelectOption {
  value: string
  label: string
}

export function Select({
  value,
  options,
  onChange,
  id,
  className = 'select',
  label,
  align = 'start',
}: {
  value: string
  options: readonly SelectOption[]
  onChange: (value: string) => void
  id?: string
  /** The trigger's look: the compact `select` by default, `input` in a form. */
  className?: string
  /** Accessible name when no <label htmlFor> points at the trigger. */
  label?: string
  /** `end` opens the list flush with the right edge — for a control near it. */
  align?: 'start' | 'end'
}) {
  const listId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const [active, setActive] = useState<number | null>(null)
  const open = active !== null
  const current = options.find((o) => o.value === value)

  useEffect(() => {
    if (!open) return
    // Optional call: jsdom has no scrollIntoView.
    listRef.current
      ?.querySelector<HTMLElement>(`[data-index="${active}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [active, open])

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setActive(null)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  function start() {
    setActive(
      Math.max(
        0,
        options.findIndex((o) => o.value === value),
      ),
    )
  }

  function pick(index: number) {
    setActive(null)
    const option = options[index]
    if (option && option.value !== value) onChange(option.value)
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const last = options.length - 1
    const move = (index: number) => {
      event.preventDefault()
      if (!open) return start()
      setActive(Math.min(Math.max(index, 0), last))
    }
    if (event.key === 'ArrowDown') move((active ?? -1) + 1)
    else if (event.key === 'ArrowUp') move((active ?? 1) - 1)
    else if (event.key === 'Home' && open) move(0)
    else if (event.key === 'End' && open) move(last)
    else if ((event.key === 'Enter' || event.key === ' ') && active !== null) {
      event.preventDefault()
      pick(active)
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      setActive(null)
    } else if (event.key === 'Tab' && open) {
      setActive(null)
    } else if (event.key.length === 1 && /\S/.test(event.key)) {
      const letter = event.key.toLowerCase()
      const from = (active ?? options.findIndex((o) => o.value === value)) + 1
      const order = [...options.slice(from), ...options.slice(0, from)]
      const hit = order.find((o) => o.label.toLowerCase().startsWith(letter))
      if (!hit) return
      const index = options.indexOf(hit)
      if (open) setActive(index)
      else if (hit.value !== value) onChange(hit.value)
    }
  }

  return (
    <div ref={rootRef} className={`dropdown${open ? ' dropdown--open' : ''}`}>
      <button
        id={id}
        type="button"
        className={`dropdown__trigger ${className}`}
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        value={value}
        onClick={() => (open ? setActive(null) : start())}
        onKeyDown={onKeyDown}
      >
        {current?.label ?? ''}
      </button>
      {open ? (
        // mousedown is cancelled so the trigger keeps focus for the keyboard.
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          className={`dropdown__list dropdown__list--${align}`}
          onMouseDown={(event) => event.preventDefault()}
        >
          {options.map((option, index) => (
            <li
              key={option.value}
              id={`${listId}-${index}`}
              data-index={index}
              role="option"
              aria-selected={option.value === value}
              className={`dropdown__option${index === active ? ' dropdown__option--active' : ''}`}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(index)}
            >
              {option.label}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
