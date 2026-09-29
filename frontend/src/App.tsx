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

import { Suspense, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { isActive, useAppState, useProposals } from './api/hooks'
import type { Spend } from './api/types'
import { useAuth } from './auth'
import { LoginPage } from './pages/LoginPage'
import { ChatIcon, LogoutIcon, ResumeIcon, SearchIcon, StarIcon } from './components/icons'
import { TelegramLink } from './components/TelegramLogin'
import { Skeleton, rub } from './components/ui'
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
  if (!token) return <LoginPage />
  return <Cabinet />
}

function Cabinet() {
  const { logout } = useAuth()
  const { data } = useAppState()
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
          {data ? <SpendMeter spend={data.spend} /> : null}
          <p className="side__promise">Отклик всегда отправляете вы сами.</p>
          {data ? <TelegramLink linkedAs={data.user.telegram_name ?? null} /> : null}
          <div className="side__user">
            <span className="side__email" title={data?.user.email}>
              {data?.user.email}
            </span>
            <button
              className="btn btn--quiet btn--small btn--icon"
              onClick={logout}
              title="Выйти"
              aria-label="Выйти"
            >
              <LogoutIcon />
            </button>
          </div>
        </div>
      </aside>

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

/** "What have I spent so far, and how much is left" — the question a paying
 *  user actually asks, answered before they hit the ceiling rather than by a
 *  402 when they do. */
function SpendMeter({ spend }: { spend: Spend }) {
  const spent = spend.spent_total_rub
  const left = spend.limit_rub
  const share = left != null && spent + left > 0 ? spent / (spent + left) : null
  return (
    <div className="meter">
      <div className="meter__row">
        <span>Потрачено</span>
        <b className="num">{rub(spent)}</b>
      </div>
      {share != null ? (
        <>
          <div
            className={share > 0.85 ? 'meter__bar meter__bar--low' : 'meter__bar'}
            role="meter"
            aria-label="Израсходовано бюджета"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(share * 100)}
          >
            <span style={{ width: `${Math.min(100, share * 100)}%` }} />
          </div>
          <div className="meter__row meter__row--sub">
            <span>Осталось</span>
            <span className="num">{rub(left ?? 0)}</span>
          </div>
        </>
      ) : null}
    </div>
  )
}
