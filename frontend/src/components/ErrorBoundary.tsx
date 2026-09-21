/** The last line: a render that throws must not leave a blank page.
 *
 * A blank cabinet reads as "nothing here" rather than as "this broke", and
 * the user has no way to tell the difference or to get out of it.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Необработанная ошибка в интерфейсе:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <main className="wrap" style={{ maxWidth: 560 }}>
        <div className="card">
          <h1 style={{ fontSize: 18, marginBottom: 8 }}>Интерфейс сломался</h1>
          <p className="muted" style={{ marginTop: 0 }}>
            Данные на сервере не пострадали — не сработал экран.
          </p>
          <pre style={{ marginBottom: 12 }}>{error.message}</pre>
          <button className="btn" onClick={() => location.reload()}>
            Перезагрузить
          </button>
        </div>
      </main>
    )
  }
}
