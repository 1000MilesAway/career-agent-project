import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastProvider } from './components/toast'
import { AuthProvider } from './auth'
import { fetcher } from './api/client'
import './styles/index.css'
import './styles/ui.css'

// Hash routing, not history: the cabinet is served as static files, and a
// path route would 404 on reload unless every server in front of it rewrites
// unknown paths to index.html. One of them is nginx in a container, the other
// is the Vite dev server, and the hash works the same in both.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <SWRConfig
        value={{
          fetcher,
          // A failed request is reported once by the caller. Retrying it
          // silently three times would hide a 402 behind a delay.
          shouldRetryOnError: false,
        }}
      >
        <ToastProvider>
          <AuthProvider>
            <HashRouter>
              <App />
            </HashRouter>
          </AuthProvider>
        </ToastProvider>
      </SWRConfig>
    </ErrorBoundary>
  </StrictMode>,
)
