/** The cabinet shell: who is logged in, which page is open.
 *
 * Three stages, three pages: describe yourself, find work, work the shortlist.
 * The route keeps the current page across a reload — a scan takes up to a
 * minute, and a refresh should not dump the user back on the resume page.
 */

import { lazy, Suspense } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useAppState, useProposals } from './api/hooks'
import { useAuth } from './auth'
import { LoginPage } from './pages/LoginPage'
import { Skeleton } from './components/ui'
import './App.css'

// The shortlist is the least-visited page and pulls in the artifact views, so
// it loads when it is opened rather than on first paint.
const SavedPage = lazy(() =>
  import('./pages/SavedPage').then((m) => ({ default: m.SavedPage })),
)
const ResumePage = lazy(() =>
  import('./pages/ResumePage').then((m) => ({ default: m.ResumePage })),
)
const SearchPage = lazy(() =>
  import('./pages/SearchPage').then((m) => ({ default: m.SearchPage })),
)
const ChatPage = lazy(() =>
  import('./pages/ChatPage').then((m) => ({ default: m.ChatPage })),
)

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

  // Without a CV the scan refuses to work, so that is where a new account
  // starts. Once one is loaded, search is the page people come back to.
  const home = data && !data.has_resume ? '/resume' : '/search'

  return (
    <div className="wrap">
      <header className="head">
        <div>
          <h1 className="head__title">career-agent</h1>
          <p className="head__sub">
            Оценка вакансий по резюме. Отклик отправляете вы сами.
          </p>
        </div>
        <div className="row">
          <span className="muted">{data?.user.email}</span>
          <button className="btn btn--ghost btn--small" onClick={logout}>
            Выйти
          </button>
        </div>
      </header>

      <nav className="nav" aria-label="Разделы">
        <NavLink className={navClass} to="/resume">
          Моё резюме
        </NavLink>
        <NavLink className={navClass} to="/search">
          Поиск
        </NavLink>
        <NavLink className={navClass} to="/saved">
          Избранное{savedCount ? ` ${savedCount}` : ''}
        </NavLink>
        {/* The count is what keeps a prepared change from being lost: the
            user may leave the chat before deciding, and nothing is written
            until they do. */}
        <NavLink className={navClass} to="/chat">
          Помощник{pendingCount ? ` ${pendingCount}` : ''}
        </NavLink>
      </nav>

      <main>
        <Suspense fallback={<Skeleton rows={5} />}>
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

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav__tab nav__tab--on' : 'nav__tab'
