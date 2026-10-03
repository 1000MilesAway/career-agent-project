import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastProvider } from './components/toast'
import { AuthProvider } from './auth'
import { fetcher } from './api/client'
import './styles/index.css'
import './styles/ui.css'

// Path routing: both servers in front of the cabinet answer an unknown path
// with index.html — nginx by try_files, the Vite dev server by default. Old
// hash links are moved into the path by the script in index.html.
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
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </AuthProvider>
        </ToastProvider>
      </SWRConfig>
    </ErrorBoundary>
  </StrictMode>,
)
