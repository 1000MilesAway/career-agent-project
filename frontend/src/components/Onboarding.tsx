/** The tour a new account sees once, right after its first CV is parsed.
 *
 * Carried in the router's location state rather than stored: it is a
 * consequence of one action — the first upload — not a setting, and a second
 * device or a cleared browser has no business showing it again. «Понятно»
 * replaces the history entry, so Back does not bring it back either.
 */

import type { ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ChatIcon, ResumeIcon, SearchIcon, StarIcon } from './icons'
import './onboarding.css'

export const ONBOARDING_STATE = { onboarding: true } as const

export function Onboarding() {
  const location = useLocation()
  const navigate = useNavigate()
  const state = location.state as { onboarding?: boolean } | null
  if (!state?.onboarding) return null

  const close = () => navigate(location.pathname, { replace: true, state: null })

  return (
    <section className="card onboarding" aria-labelledby="onboarding-title">
      <h2 id="onboarding-title" className="onboarding__title">
        Резюме разобрано — теперь можно искать
      </h2>
      <p className="muted onboarding__lede">
        По резюме уже собраны запросы и фильтры. Коротко, где что в кабинете:
      </p>

      <ul className="onboarding__list">
        <Step icon={<SearchIcon />} title="Поиск">
          Задайте зарплату и нажмите «Найти вакансии»: сбор со всех источников и отсев по
          фильтрам бесплатны. Платно работает только модель — «Отсеять моделью» и оценка
          отдельной вакансии, и только по тому, что прошло отбор. Отсеянное не пропадает:
          оно под фильтром и с причиной.
        </Step>
        <Step icon={<StarIcon />} title="Избранное">
          Звёздочкой сохраняете вакансию. Там — резюме под неё, сопроводительное письмо,
          подготовка к интервью и статус отклика.
        </Step>
        <Step icon={<ChatIcon />} title="Помощник">
          Чат, чтобы поправить резюме или условия поиска. Помощник только предлагает —
          изменение записывается, когда вы нажмёте «Принять».
        </Step>
        <Step icon={<ResumeIcon />} title="Моё резюме">
          Как модель прочитала ваше резюме и по каким спискам идёт отсев. Проверьте его — на
          этом строится каждая оценка.
        </Step>
      </ul>

      <p className="onboarding__note">
        Расход виден внизу слева. Откликаетесь вы сами — сервис ничего не отправляет
        работодателям.
      </p>

      <button type="button" className="btn" onClick={close}>
        Понятно
      </button>
    </section>
  )
}

function Step({
  icon,
  title,
  children,
}: {
  icon: ReactNode
  title: string
  children: ReactNode
}) {
  return (
    <li className="onboarding__step">
      <span className="onboarding__icon">{icon}</span>
      <div>
        <b>{title}</b>
        <p>{children}</p>
      </div>
    </li>
  )
}
