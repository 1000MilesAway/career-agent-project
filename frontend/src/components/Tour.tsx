/** The first-run hints, one at a time, each at the thing it is about.
 *
 * A step ends by doing what it points at, not only by «Понятно»: opening
 * the search moves the tour on, and saving any vacancy finishes it.
 */

import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { Coachmark } from './Coachmark'
import { setTourStep, useTourStep } from './tourStep'

export function Tour({ savedCount }: { savedCount: number }) {
  const step = useTourStep()
  const { pathname } = useLocation()

  useEffect(() => {
    if (step === 'search' && pathname === '/search') setTourStep('star')
    if (step === 'star' && savedCount > 0) setTourStep(null)
  }, [step, pathname, savedCount])

  if (step === 'search' && pathname !== '/search')
    return (
      <Coachmark
        target='.side__nav a[href="/search"]'
        title="Резюме готово — пора искать"
        onClose={() => setTourStep('star')}
      >
        Откройте «Поиск»: соберём вакансии с разных источников и отберём те, что подходят
        под ваш опыт.
      </Coachmark>
    )

  // The star appears only once a scan has found something, so the hint
  // waits for the list instead of pointing at an empty page.
  if (step === 'star' && pathname === '/search' && savedCount === 0)
    return (
      <Coachmark
        target=".vrow .star"
        title="Понравилась вакансия?"
        onClose={() => setTourStep(null)}
      >
        Нажмите на звёздочку — вакансия сохранится в «Избранном». Там под неё готовятся
        резюме, сопроводительное письмо и подготовка к интервью.
      </Coachmark>
    )

  return null
}
