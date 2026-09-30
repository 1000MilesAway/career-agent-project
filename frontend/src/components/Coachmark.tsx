/** A hint that points at one element on the page.
 *
 * The target is found by selector rather than passed as a ref: the elements
 * it points at (a sidebar link, a star in a list row) belong to components
 * that know nothing about the tour. Rendered in a portal with fixed
 * position — the sidebar scrolls sideways on a phone and would clip it.
 * While the target is not on the page the hint waits rather than guessing
 * a place.
 */

import { type ReactNode, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import './coachmark.css'

const GAP = 12
const MARGIN = 16

type Place = { top: number; left: number; side: 'right' | 'bottom'; arrow: number }
type Box = { top: number; left: number; width: number; height: number }

export function Coachmark({
  target,
  title,
  children,
  onClose,
}: {
  target: string
  title: string
  children: ReactNode
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<Box | null>(null)
  const [place, setPlace] = useState<Place | null>(null)

  useLayoutEffect(() => {
    let frame = 0
    const measure = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const element = document.querySelector(target)
        const rect = element?.getBoundingClientRect()
        const hint = ref.current
        if (!rect || !rect.width || !hint) {
          setBox(null)
          setPlace(null)
          return
        }
        setBox({ top: rect.top, left: rect.left, width: rect.width, height: rect.height })
        setPlace(placeNear(rect, hint.offsetWidth, hint.offsetHeight))
      })
    }
    measure()
    // The list under the hint re-renders after a scan; a new row is a new
    // target, and nothing else would tell this component it moved.
    const observer = new MutationObserver(measure)
    observer.observe(document.body, { childList: true, subtree: true })
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [target])

  return createPortal(
    <>
      {box ? (
        <span
          className="coach__ring"
          aria-hidden="true"
          style={{
            top: box.top - 4,
            left: box.left - 4,
            width: box.width + 8,
            height: box.height + 8,
          }}
        />
      ) : null}
      <div
        ref={ref}
        className={place ? `coach coach--${place.side}` : 'coach coach--hidden'}
        role="dialog"
        aria-label={title}
        style={place ? { top: place.top, left: place.left } : undefined}
      >
        {place ? (
          <span
            className="coach__arrow"
            style={place.side === 'right' ? { top: place.arrow } : { left: place.arrow }}
          />
        ) : null}
        <p className="coach__title">{title}</p>
        <p className="coach__text">{children}</p>
        <button type="button" className="btn btn--small" onClick={onClose}>
          Понятно
        </button>
      </div>
    </>,
    document.body,
  )
}

/** Beside the target when there is room (the sidebar), under it otherwise
 *  (a star at the right edge of a row, or the top bar on a phone). */
function placeNear(rect: DOMRect, width: number, height: number): Place {
  const clamp = (value: number, min: number, max: number) =>
    Math.max(min, Math.min(value, max))
  const centerY = rect.top + rect.height / 2
  const centerX = rect.left + rect.width / 2
  if (rect.right + GAP + width + MARGIN <= window.innerWidth) {
    const top = clamp(centerY - 28, MARGIN, window.innerHeight - height - MARGIN)
    return { side: 'right', left: rect.right + GAP, top, arrow: centerY - top }
  }
  const left = clamp(centerX - width + 36, MARGIN, window.innerWidth - width - MARGIN)
  return { side: 'bottom', top: rect.bottom + GAP, left, arrow: centerX - left }
}
