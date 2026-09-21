/** Transient messages.
 *
 * The old page wrote every outcome — an error, a triage summary, a failed
 * save — into whatever status <div> was nearest, so a message could land on a
 * page the user had already left, or be overwritten by the next one. A toast
 * is attached to the action instead of to a spot in the markup.
 *
 * Errors do not auto-dismiss: a message that disappears before it is read is
 * the same as no message.
 */

import {
  createContext,
  use,
  useCallback,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import './toast.css'

type Tone = 'info' | 'error'

interface Toast {
  id: number
  tone: Tone
  text: string
}

interface ToastValue {
  info: (text: string) => void
  error: (error: unknown) => void
}

const ToastContext = createContext<ToastValue | null>(null)

const AUTO_DISMISS_MS = 6000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (tone: Tone, text: string) => {
      const id = nextId.current++
      setToasts((current) => [...current, { id, tone, text }])
      if (tone === 'info') {
        window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
      }
    },
    [dismiss],
  )

  const value = useMemo<ToastValue>(
    () => ({
      info: (text) => push('info', text),
      error: (error) =>
        push('error', error instanceof Error ? error.message : String(error)),
    }),
    [push],
  )

  return (
    <ToastContext value={value}>
      {children}
      {/* Polite, not assertive: these report the result of something the user
          just did, and should not interrupt what a screen reader is saying. */}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span>{toast.text}</span>
            <button
              type="button"
              className="toast__close"
              onClick={() => dismiss(toast.id)}
              aria-label="Закрыть сообщение"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext>
  )
}

export function useToast(): ToastValue {
  const value = use(ToastContext)
  if (!value) throw new Error('useToast вызван вне ToastProvider')
  return value
}
