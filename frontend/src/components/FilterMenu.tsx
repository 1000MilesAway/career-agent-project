/** A dropdown of rarely touched filters.
 *
 * Built on <details>: open state, keyboard toggling and the disclosure role
 * come from the browser. What it lacks is closing on a click elsewhere or on
 * Escape, which a popover is expected to do — added below.
 */

import { useEffect, useRef, type ReactNode } from 'react'
import { ChevronIcon } from './icons'
import './filtermenu.css'

export function FilterMenu({
  label,
  active,
  children,
}: {
  label: string
  /** How many options inside are on: shown on the closed button, so a filter
   *  that narrows the search is never out of sight. */
  active: number
  children: ReactNode
}) {
  const ref = useRef<HTMLDetailsElement>(null)

  // Document listeners, not data: an effect is the place for them.
  useEffect(() => {
    function onPointer(event: PointerEvent) {
      const element = ref.current
      if (element?.open && !element.contains(event.target as Node)) element.open = false
    }
    function onKey(event: KeyboardEvent) {
      const element = ref.current
      if (event.key !== 'Escape' || !element?.open) return
      element.open = false
      element.querySelector('summary')?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <details ref={ref} className="fmenu">
      <summary className={active ? 'fmenu__btn fmenu__btn--on' : 'fmenu__btn'}>
        {label}
        {active ? <span className="fmenu__count num">{active}</span> : null}
        <ChevronIcon />
      </summary>
      <div className="fmenu__panel">{children}</div>
    </details>
  )
}
