/** The landing's one behaviour: the slide in the middle of the viewport picks
 * which screen the sticky device shows.
 *
 * An observer rather than a scroll handler: it fires only when a slide crosses
 * the centre line, so the page does no work while the user simply scrolls.
 * Plain DOM, no React — the landing is static markup in index.html and must
 * not wait for the cabinet's bundle to come alive.
 */

const slides = document.querySelectorAll<HTMLElement>('.l-slide')
const screens = document.querySelectorAll<HTMLElement>('.l-screen')
const landing = document.getElementById('landing')
const counter = document.querySelector<HTMLElement>('.l-counter b')

function show(index: number) {
  screens.forEach((screen) => {
    screen.classList.toggle('is-active', Number(screen.dataset.screen) === index)
  })
  slides.forEach((slide) => {
    slide.classList.toggle('is-active', Number(slide.dataset.slide) === index)
  })
  if (counter) counter.textContent = String(index + 1).padStart(2, '0')
}

const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) show(Number((entry.target as HTMLElement).dataset.slide))
    }
  },
  // A one-pixel band at 70% of the height: exactly one slide crosses it at a
  // time. Not the middle — on a phone the device covers the upper half, and
  // the slide being read is the one below it.
  { rootMargin: '-70% 0px -30% 0px' },
)

slides.forEach((slide) => observer.observe(slide))
show(0)

// Elements that animate on arrival, outside the deck.
const reveal = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      entry.target.classList.toggle('is-in', entry.isIntersecting)
    }
  },
  { threshold: 0.35 },
)
document.querySelectorAll('.l-hero, .l-final').forEach((el) => reveal.observe(el))

// The header turns dark when the dark final screen reaches it — a thin band
// along the top edge, where the header sits.
const final = document.querySelector('.l-final')
if (final) {
  new IntersectionObserver(
    ([entry]) => landing?.classList.toggle('l-on-dark', entry.isIntersecting),
    { rootMargin: '0px 0px -93% 0px' },
  ).observe(final)
}
