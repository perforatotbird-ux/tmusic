import React from 'react'
import { createLogger } from '../../utils/logger'

const log = createLogger('error-boundary')

/**
 * UI-02: the app had no error boundary at all — any render error unmounted the
 * whole tree and left a blank white screen with no explanation and no recovery
 * action. This renders a readable fallback with retry instead.
 *
 * `onReset` lets the host clear whatever state caused the crash (e.g. drop a
 * cached payload) before remounting children.
 */
export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    this.setState({ info })
    log.error('render error caught by boundary', {
      message: error?.message,
      stack: error?.stack,
      componentStack: info?.componentStack,
    })
  }

  handleReset = () => {
    this.setState({ error: null, info: null })
    try {
      this.props.onReset?.()
    } catch (e) {
      log.warn('onReset handler failed', { message: e?.message })
    }
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children

    const label = this.props.label || 'приложения'

    return (
      <div
        role="alert"
        style={{
          minHeight: '100dvh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 14,
          padding: 24,
          textAlign: 'center',
          background: 'var(--bg)',
          color: 'var(--ink)',
        }}
      >
        <div style={{ fontSize: 18, fontWeight: 700 }}>Что-то пошло не так</div>
        <div style={{ fontSize: 13.5, color: 'var(--dim)', maxWidth: 460 }}>
          Интерфейс {label} не смог отрисоваться. Вы можете попробовать снова —
          воспроизведение и очередь будут восстановлены из сохранённой сессии.
        </div>
        <pre
          style={{
            maxWidth: '92vw',
            maxHeight: 180,
            overflow: 'auto',
            padding: '10px 12px',
            borderRadius: 8,
            background: 'var(--bg-card)',
            border: '1px solid var(--line)',
            color: 'var(--danger)',
            fontSize: 12,
            textAlign: 'left',
            whiteSpace: 'pre-wrap',
            userSelect: 'text',
          }}
        >
          {String(error?.message || error)}
        </pre>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center' }}>
          <button
            type="button"
            onClick={this.handleReset}
            style={{
              minHeight: 44,
              padding: '0 18px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--accent)',
              color: 'var(--on-accent)',
              fontWeight: 700,
              fontSize: 14,
              cursor: 'pointer',
            }}
          >
            Попробовать снова
          </button>
          <button
            type="button"
            onClick={this.handleReload}
            style={{
              minHeight: 44,
              padding: '0 18px',
              borderRadius: 8,
              border: '1px solid var(--line-bright)',
              background: 'var(--bg-card)',
              color: 'var(--ink)',
              fontWeight: 600,
              fontSize: 14,
              cursor: 'pointer',
            }}
          >
            Перезагрузить страницу
          </button>
        </div>
        {info?.componentStack && (
          <details style={{ maxWidth: '92vw', fontSize: 11.5, color: 'var(--dim)' }}>
            <summary style={{ cursor: 'pointer' }}>Подробности</summary>
            <pre style={{ whiteSpace: 'pre-wrap', userSelect: 'text', marginTop: 8 }}>
              {info.componentStack}
            </pre>
          </details>
        )}
      </div>
    )
  }
}
