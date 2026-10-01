/** Dragging a shortlisted row onto a stage of the funnel.
 *
 * Built on pointer events rather than HTML5 drag and drop. Native drag hands
 * the look to the browser: the cursor is a "no entry" sign over everything
 * that is not a drop zone, CSS `cursor` is ignored for the whole drag, and the
 * carried row is a translucent screenshot. It also does not work with a finger
 * on most phones. Here the page draws the carried card itself, the cursor is
 * a closed hand throughout, and touch starts with a long press so an ordinary
 * swipe still scrolls the list.
 *
 * Stages are found by `data-stage` under the pointer; the carried card has
 * `pointer-events: none`, so it never hides the stage it is held over.
 */

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

/** Mouse travel before a press becomes a drag, so a click stays a click. */
const THRESHOLD = 6
/** A finger has to rest this long; moving earlier means the user scrolls. */
const LONG_PRESS_MS = 350
/** Where the pointer sits inside the carried card. */
export const GRIP = { x: 28, y: 22 }

/** Presses on controls inside the row are theirs, not the start of a drag. */
const INTERACTIVE = 'a, button, select, input, textarea, label'

export type Held = { id: string; x: number; y: number }

export function useStageDrag(onDrop: (id: string, stage: string) => void) {
  const [held, setHeld] = useState<Held | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const ghostRef = useRef<HTMLDivElement>(null)
  const stopRef = useRef<(() => void) | null>(null)

  // Leaving the page mid-drag must not leave listeners and the hand cursor.
  useEffect(() => () => stopRef.current?.(), [])

  function onPointerDown(e: ReactPointerEvent<HTMLElement>, id: string) {
    if (e.button !== 0 || (e.target as Element).closest(INTERACTIVE)) return
    stopRef.current?.()

    const touch = e.pointerType === 'touch'
    const origin = { x: e.clientX, y: e.clientY }
    let last = origin
    let active = false
    let stage: string | null = null
    let timer: ReturnType<typeof setTimeout> | undefined

    const place = (x: number, y: number) => {
      const ghost = ghostRef.current
      if (ghost) ghost.style.transform = `translate(${x - GRIP.x}px, ${y - GRIP.y}px)`
      const hit = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-stage]')
      const next = hit?.dataset.stage ?? null
      if (next !== stage) {
        stage = next
        setOver(next)
      }
    }

    const activate = () => {
      active = true
      document.documentElement.classList.add('is-dragging')
      setHeld({ id, x: last.x, y: last.y })
      if (touch) navigator.vibrate?.(10)
      place(last.x, last.y)
    }

    const move = (x: number, y: number) => {
      last = { x, y }
      if (active) return place(x, y)
      if (Math.hypot(x - origin.x, y - origin.y) < THRESHOLD) return
      // A finger that moves before the long press is scrolling the page.
      if (touch) return stop()
      activate()
    }

    const onMove = (ev: PointerEvent) => move(ev.clientX, ev.clientY)
    // Once the drag holds the finger the page must not scroll under it; only
    // a non-passive touchmove can say so.
    const onTouchMove = (ev: TouchEvent) => {
      if (active) ev.preventDefault()
    }
    const onUp = () => {
      const target = active ? stage : null
      stop()
      if (target) onDrop(id, target)
    }
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') stop()
    }
    // A long press opens the context menu on phones; it would end the drag.
    const onMenu = (ev: Event) => ev.preventDefault()

    function stop() {
      clearTimeout(timer)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', stop)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('contextmenu', onMenu)
      document.documentElement.classList.remove('is-dragging')
      stopRef.current = null
      if (active) {
        setHeld(null)
        setOver(null)
      }
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', stop)
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('keydown', onKey)
    if (touch) {
      window.addEventListener('contextmenu', onMenu)
      timer = setTimeout(activate, LONG_PRESS_MS)
    }
    stopRef.current = stop
  }

  return { held, over, ghostRef, onPointerDown }
}
