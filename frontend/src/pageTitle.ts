/** Tab titles: «Поиск вакансий | Career Helper» and so on, one per route. */

import { LEGAL_TITLES } from './pages/LegalPage'

const SITE_NAME = 'Career Helper'

const PAGE_TITLES: Record<string, string> = {
  '/search': 'Поиск вакансий',
  '/added': 'Мои вакансии',
  '/saved': 'Избранное',
  '/chat': 'Помощник',
  '/resume': 'Моё резюме',
  '/terms': LEGAL_TITLES.terms,
  '/privacy': LEGAL_TITLES.privacy,
  '/verify': 'Подтверждение почты',
  '/reset-password': 'Новый пароль',
  '/admin/users': 'Пользователи',
  '/admin/spend': 'Траты на модель',
  '/admin/funnel': 'Конверсия',
  '/admin/sources': 'Источники',
}

const OPEN_PATHS = new Set(['/verify', '/reset-password', '/terms', '/privacy'])

/** The tab title for what Screen shows at this path. Mirrors its branches:
 * without a token every path but the open ones is the login form, and the
 * bare root is the landing, which carries the site name alone. */
export function pageTitle(pathname: string, loggedIn: boolean): string {
  const open = OPEN_PATHS.has(pathname)
  if (!loggedIn && pathname === '/') return SITE_NAME
  const page = loggedIn || open ? PAGE_TITLES[pathname] : 'Вход'
  return page ? `${page} | ${SITE_NAME}` : SITE_NAME
}
