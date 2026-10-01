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

import { Suspense, useState, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { isActive, useAppState, useProposals } from './api/hooks'
import type { Quota, QuotaWindow } from './api/types'
import { useAuth } from './auth'
import { LoginPage } from './pages/LoginPage'
import { VerifyPage } from './pages/VerifyPage'
import { ChatIcon, ResumeIcon, SearchIcon, StarIcon } from './components/icons'
import { Tour } from './components/Tour'
import { Avatar, ProfileDialog, displayName } from './components/ProfileDialog'
import { Skeleton } from './components/ui'
import { useScanWatcher } from './components/useScanWatcher'
import { lazyPage } from './lazyPage'
import './App.css'

// The shortlist is the least-visited page and pulls in the artifact views, so
// it loads when it is opened rather than on first paint.
const SavedPage = lazyPage(() => import('./pages/SavedPage').then((m) => m.SavedPage))
const ResumePage = lazyPage(() => import('./pages/ResumePage').then((m) => m.ResumePage))
const SearchPage = lazyPage(() => import('./pages/SearchPage').then((m) => m.SearchPage))
const ChatPage = lazyPage(() => import('./pages/ChatPage').then((m) => m.ChatPage))

export function App() {
  const { token } = useAuth()
  const { pathname } = useLocation()
  // Before the token check: the link from a letter must work whether or not
  // this browser is logged in, and logged in as whom.
  if (pathname === '/verify') return <VerifyPage />
  if (!token) return <LoginPage />
  return <Cabinet />
}

function Cabinet() {
  const { data } = useAppState()
  const [profileOpen, setProfileOpen] = useState(false)
  const { data: proposals } = useProposals()
  const savedCount = data?.found.filter((row) => row.saved).length ?? 0
  const pendingCount = proposals?.length ?? 0
  // Here, not on the search page: a scan runs on the server and the user is
  // free to leave the page while it does.
  const scanning = isActive(useScanWatcher())

  // Without a CV the scan refuses to work, so that is where a new account
  // starts. Once one is loaded, search is the page people come back to.
  const home = data && !data.has_resume ? '/resume' : '/search'

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">career-agent</span>
        </div>

        <nav className="side__nav" aria-label="Разделы">
          <NavItem to="/search" icon={<SearchIcon />} busy={scanning}>
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
        </nav>

        <div className="side__foot">
          {data ? <QuotaMeter quota={data.spend.quota} /> : null}
          <p className="side__promise">Отклик всегда отправляете вы сами.</p>
          {data ? (
            <>
              {/* The name opens the account: settings, Telegram, logout —
                  all rarely used, so none of it sits in the sidebar itself. */}
              <button
                type="button"
                className="side__user"
                onClick={() => setProfileOpen(true)}
                title="Профиль и настройки"
                aria-label={`Профиль: ${displayName(data.user)}`}
                aria-haspopup="dialog"
              >
                <Avatar user={data.user} />
                <span className="side__name">{displayName(data.user)}</span>
              </button>
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
            <Route path="/saved" element={<SavedPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="*" element={<Navigate to={home} replace />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
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
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? 'nav nav--on' : 'nav')}>
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
