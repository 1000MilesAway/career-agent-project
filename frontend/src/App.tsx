/** The cabinet shell: who is logged in, which page is open, what it has cost.
 *
 * Four stages, four pages: describe yourself, find work, work the shortlist,
 * talk it through. The route keeps the current page across a reload — a scan
 * takes up to a minute, and a refresh should not dump the user back on the
 * resume page.
 *
 * A sidebar rather than tabs over the content: the pages are places the user
 * moves between all session, and the counts beside them (saved vacancies,
 * changes waiting for a decision) should stay in view while they work.
 */

import {
  Suspense,
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type ReactNode,
} from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { isActive, useAppState, useProposals } from './api/hooks'
import type { AppState, Quota, QuotaWindow } from './api/types'
import { useAuth } from './auth'
import { LoginPage } from './pages/LoginPage'
import { VerifyPage } from './pages/VerifyPage'
import { ResetPasswordPage } from './pages/ResetPasswordPage'
import { LEGAL_TITLES, LegalPage, LegalScreen } from './pages/LegalPage'
import { pageTitle } from './pageTitle'
import {
  BrandMark,
  ChartIcon,
  ChatIcon,
  FunnelIcon,
  InfoIcon,
  LogoutIcon,
  MoonIcon,
  SettingsIcon,
  SunIcon,
  AutoThemeIcon,
  ResumeIcon,
  SearchIcon,
  SourcesIcon,
  StarIcon,
  UsersIcon,
} from './components/icons'
import { Tour } from './components/Tour'
import { Avatar, ProfileDialog, displayName } from './components/ProfileDialog'
import { SupportBubble } from './components/SupportBubble'
import { Skeleton } from './components/ui'
import { useScanWatcher } from './components/useScanWatcher'
import { useTriageWatcher } from './components/useTriageWatcher'
import { useTheme, type Theme } from './components/theme'
import { lazyPage } from './lazyPage'
import './App.css'

// The shortlist is the least-visited page and pulls in the artifact views, so
// it loads when it is opened rather than on first paint.
const SavedPage = lazyPage(() => import('./pages/SavedPage').then((m) => m.SavedPage))
const ResumePage = lazyPage(() => import('./pages/ResumePage').then((m) => m.ResumePage))
const SearchPage = lazyPage(() => import('./pages/SearchPage').then((m) => m.SearchPage))
const ChatPage = lazyPage(() => import('./pages/ChatPage').then((m) => m.ChatPage))
// The owner's dashboard: separate chunks, so no user downloads it.
const AdminUsersPage = lazyPage(() =>
  import('./pages/AdminUsersPage').then((m) => m.AdminUsersPage),
)
const AdminSpendPage = lazyPage(() =>
  import('./pages/AdminSpendPage').then((m) => m.AdminSpendPage),
)
const AdminFunnelPage = lazyPage(() =>
  import('./pages/AdminFunnelPage').then((m) => m.AdminFunnelPage),
)
const AdminSourcesPage = lazyPage(() =>
  import('./pages/AdminSourcesPage').then((m) => m.AdminSourcesPage),
)

export function App() {
  return (
    <>
      <Screen />
      <SupportBubble />
    </>
  )
}

function Screen() {
  const { token } = useAuth()
  const { pathname } = useLocation()
  // An effect, not React 19's hoisted <title>: index.html already has one,
  // and the browser keeps the first <title> in the head.
  useEffect(() => {
    document.title = pageTitle(pathname, Boolean(token))
  }, [pathname, token])
  // Before the token check: the link from a letter must work whether or not
  // this browser is logged in, and logged in as whom.
  if (pathname === '/verify') return <VerifyPage />
  if (pathname === '/reset-password') return <ResetPasswordPage />
  if (!token && pathname === '/terms') return <LegalScreen doc="terms" />
  if (!token && pathname === '/privacy') return <LegalScreen doc="privacy" />
  if (!token) return <LoginPage />
  return <Cabinet />
}

function Cabinet() {
  const { data } = useAppState()
  const [profileOpen, setProfileOpen] = useState(false)
  const { data: proposals } = useProposals()
  const savedCount = data?.found.filter((row) => row.saved).length ?? 0
  const pendingCount = proposals?.length ?? 0
  // Here, not on the search page: a scan and a triage run on the server and
  // the user is free to leave the page while they do.
  const scanning = isActive(useScanWatcher())
  const triaging = isActive(useTriageWatcher())

  // Without a CV the scan refuses to work, so that is where a new account
  // starts. Once one is loaded, search is the page people come back to.
  const home = data && !data.has_resume ? '/resume' : '/search'

  // On a phone the sections are a strip that scrolls sideways and the last
  // ones start off screen: the current one is brought into view, or a user
  // on «Моё резюме» sees no sign of where they are.
  const nav = useRef<HTMLElement>(null)
  const { pathname } = useLocation()
  // Again once the counts and fonts arrive: they widen the items before it.
  const isAdmin = Boolean(data?.user.is_admin)
  useEffect(() => {
    const reveal = () =>
      nav.current
        ?.querySelector('.nav--on')
        ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    reveal()
    void document.fonts?.ready.then(reveal)
  }, [pathname, savedCount, pendingCount, isAdmin])

  return (
    <div className={isRail(pathname) ? 'shell shell--rail' : 'shell'}>
      <aside className="side">
        <div className="brand">
          <BrandMark />
          <span className="brand__name">gethire</span>
        </div>

        <nav className="side__nav" aria-label="Разделы" ref={nav}>
          <NavItem to="/search" icon={<SearchIcon />} busy={scanning || triaging}>
            Поиск
          </NavItem>
          <NavItem to="/saved" icon={<StarIcon />} count={savedCount}>
            Избранное
          </NavItem>
          {/* The count is what keeps a prepared change from being lost: the
              user may leave the chat before deciding, and nothing is written
              until they do. */}
          <NavItem to="/chat" icon={<ChatIcon />} count={pendingCount} attention>
            Помощник
          </NavItem>
          <NavItem to="/resume" icon={<ResumeIcon />}>
            Моё резюме
          </NavItem>
          {data?.user.is_admin ? (
            <>
              <span className="side__group" aria-hidden="true">
                Админка
              </span>
              <NavItem to="/admin/users" icon={<UsersIcon />}>
                Пользователи
              </NavItem>
              <NavItem to="/admin/funnel" icon={<FunnelIcon />}>
                Конверсия
              </NavItem>
              <NavItem to="/admin/spend" icon={<ChartIcon />}>
                Траты
              </NavItem>
              <NavItem to="/admin/sources" icon={<SourcesIcon />}>
                Источники
              </NavItem>
            </>
          ) : null}
        </nav>

        <div className="side__foot">
          {data ? <QuotaMeter quota={data.spend.quota} /> : null}
          {data ? (
            <>
              <UserMenu user={data.user} onProfile={() => setProfileOpen(true)} />
              <ProfileDialog
                open={profileOpen}
                user={data.user}
                onClose={() => setProfileOpen(false)}
              />
            </>
          ) : null}
        </div>
      </aside>

      <Tour savedCount={savedCount} />

      <main className="main">
        <Suspense
          fallback={
            <div className="page">
              <Skeleton rows={5} />
            </div>
          }
        >
          <Routes>
            <Route path="/resume" element={<ResumePage />} />
            <Route path="/search" element={<SearchPage />} />
            {/* «Мои вакансии» was folded into «Избранное» (2026-10-08). */}
            <Route path="/added" element={<Navigate to="/saved" replace />} />
            <Route path="/saved" element={<SavedPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/chat/:threadId" element={<ChatPage />} />
            <Route
              path="/admin/users"
              element={<AdminOnly state={data} page={<AdminUsersPage />} />}
            />
            <Route
              path="/admin/funnel"
              element={<AdminOnly state={data} page={<AdminFunnelPage />} />}
            />
            <Route
              path="/admin/spend"
              element={<AdminOnly state={data} page={<AdminSpendPage />} />}
            />
            <Route
              path="/admin/sources"
              element={<AdminOnly state={data} page={<AdminSourcesPage />} />}
            />
            <Route path="/terms" element={<LegalPage doc="terms" />} />
            <Route path="/privacy" element={<LegalPage doc="privacy" />} />
            <Route path="*" element={<Navigate to={home} replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}

/** Pages with a list of their own in the sidebar's place: the sections fold
 *  into a strip of icons, so there is one sidebar on screen, not two. */
function isRail(pathname: string) {
  return pathname === '/chat' || pathname.startsWith('/chat/')
}

/** An admin page, or the cabinet's home for anyone else. Waits for the
 *  state rather than redirecting on its absence: a reload of /admin/spend
 *  must land back there, not on search. The API refuses non-admins anyway;
 *  this only keeps them from a page of 403s. */
function AdminOnly({ state, page }: { state: AppState | undefined; page: ReactNode }) {
  if (!state) {
    return (
      <div className="page">
        <Skeleton rows={5} />
      </div>
    )
  }
  return state.user.is_admin ? page : <Navigate to="/search" replace />
}

function NavItem({
  to,
  icon,
  count = 0,
  attention = false,
  busy = false,
  children,
}: {
  to: string
  icon: ReactNode
  count?: number
  /** A count that asks for action, not one that merely tallies. */
  attention?: boolean
  /** Something runs in the background for this page. */
  busy?: boolean
  children: ReactNode
}) {
  // In the strip the label is hidden, so the name comes back on hover.
  const rail = isRail(useLocation().pathname)
  return (
    <NavLink
      to={to}
      className={({ isActive }) => (isActive ? 'nav nav--on' : 'nav')}
      title={rail && typeof children === 'string' ? children : undefined}
    >
      {icon}
      <span className="nav__label">{children}</span>
      {busy ? (
        <span className="nav__busy" role="img" title="Идёт поиск" aria-label="идёт поиск" />
      ) : null}
      {count ? (
        <span className={attention ? 'nav__count nav__count--hot' : 'nav__count'}>
          {count}
        </span>
      ) : null}
    </NavLink>
  )
}

/** "How much can I still do, and when does it come back" — answered in
 *  shares, never money, before the user hits the ceiling rather than by a
 *  402 when they do. The week is the budget; the day stops one evening from
 *  eating it. */
function QuotaMeter({ quota }: { quota: Quota }) {
  const plan =
    quota.plan === 'paid' && quota.paid_until
      ? `Подписка до ${moscowDate(quota.paid_until, { day: 'numeric', month: 'long' })}`
      : 'Бесплатный тариф'
  return (
    <div className="meter">
      <div className="meter__row">
        <b>{plan}</b>
      </div>
      {quota.day ? (
        <QuotaBar label="Сегодня" win={quota.day} resets="обновится в 00:00" />
      ) : null}
      {quota.week ? (
        <QuotaBar
          label="Неделя"
          win={quota.week}
          resets={`обновится ${moscowDate(quota.week.resets_at, { weekday: 'short', day: 'numeric', month: 'short' })}`}
        />
      ) : null}
    </div>
  )
}

function QuotaBar({
  label,
  win,
  resets,
}: {
  label: string
  win: QuotaWindow
  resets: string
}) {
  const pct = win.used_pct
  return (
    <>
      <div className="meter__row">
        <span>{label}</span>
        <span className="num">{pct}%</span>
      </div>
      <div
        className={pct >= 85 ? 'meter__bar meter__bar--low' : 'meter__bar'}
        role="meter"
        aria-label={`${label}: израсходовано лимита`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      {pct >= 100 ? <div className="meter__row meter__row--sub">{resets}</div> : null}
    </>
  )
}

/** Dates on the Moscow clock: that is where the quota periods turn over. */
const moscowDate = (iso: string, options: Intl.DateTimeFormatOptions): string =>
  new Date(iso).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow', ...options })

/** The name in the corner opens a short menu: the account, and the documents
 *  that are needed rarely but must be one click away from anywhere. */
function UserMenu({ user, onProfile }: { user: AppState['user']; onProfile: () => void }) {
  const [open, setOpen] = useState(false)
  const [legalOpen, setLegalOpen] = useState(false)
  const { logout } = useAuth()

  // Focus leaving the menu closes it: a click elsewhere or Tab past the end.
  function onBlur(event: FocusEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
  }

  return (
    <div
      className="user-menu"
      onBlur={onBlur}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setOpen(false)
      }}
    >
      {open ? (
        <div className="user-menu__list" role="menu">
          <button
            type="button"
            role="menuitem"
            className="user-menu__item user-menu__item--icon"
            onClick={() => {
              setOpen(false)
              onProfile()
            }}
          >
            <SettingsIcon />
            Профиль и настройки
          </button>
          {/* Folded into a side menu: the documents are read once, the
              profile far more often. Opens on hover, and on click for touch. */}
          <div
            className="user-menu__subwrap"
            onMouseEnter={() => setLegalOpen(true)}
            onMouseLeave={() => setLegalOpen(false)}
          >
            <button
              type="button"
              role="menuitem"
              className="user-menu__item user-menu__item--icon user-menu__sub"
              onClick={() => setLegalOpen(!legalOpen)}
              aria-haspopup="menu"
              aria-expanded={legalOpen}
            >
              <InfoIcon />
              Инфо
              <span className="user-menu__arrow" aria-hidden="true">
                ▸
              </span>
            </button>
            {legalOpen ? (
              <div className="user-menu__list user-menu__list--side" role="menu">
                {(['terms', 'privacy'] as const).map((doc) => (
                  <NavLink
                    key={doc}
                    to={`/${doc}`}
                    role="menuitem"
                    className="user-menu__item"
                    onClick={() => setOpen(false)}
                  >
                    {LEGAL_TITLES[doc]}
                  </NavLink>
                ))}
              </div>
            ) : null}
          </div>
          <ThemeSwitch />
          {/* Last and set apart: the one item that ends the session. */}
          <button
            type="button"
            role="menuitem"
            className="user-menu__item user-menu__item--icon user-menu__logout"
            onClick={logout}
          >
            <LogoutIcon />
            Выйти
          </button>
        </div>
      ) : null}
      <button
        type="button"
        className="side__user"
        onClick={() => setOpen(!open)}
        title="Профиль и документы"
        aria-label={`Меню: ${displayName(user)}`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Avatar user={user} />
        <span className="side__name">{displayName(user)}</span>
        <span className="side__caret" aria-hidden="true">
          ▾
        </span>
      </button>
    </div>
  )
}

const THEMES: { value: Theme; label: string; icon: ReactNode }[] = [
  { value: 'auto', label: 'Как в системе', icon: <AutoThemeIcon /> },
  { value: 'light', label: 'Светлая', icon: <SunIcon /> },
  { value: 'dark', label: 'Тёмная', icon: <MoonIcon /> },
]

/** Three icons in a row rather than a submenu: the choice is seen at a glance
 *  and made in one click, and the menu stays open to show the result. */
function ThemeSwitch() {
  const [theme, setTheme] = useTheme()
  return (
    <div className="user-menu__theme" role="group" aria-label="Тема">
      <span>Тема</span>
      <span className="theme-switch">
        {THEMES.map((option) => (
          <button
            key={option.value}
            type="button"
            className={
              option.value === theme
                ? 'theme-switch__btn theme-switch__btn--on'
                : 'theme-switch__btn'
            }
            onClick={() => setTheme(option.value)}
            aria-pressed={option.value === theme}
            aria-label={option.label}
            title={option.label}
          >
            {option.icon}
          </button>
        ))}
      </span>
    </div>
  )
}
