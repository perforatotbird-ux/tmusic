import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { SharePage } from './components/SharePage'
import { ErrorBoundary } from './components/ui/ErrorBoundary'
import { installGlobalErrorLogging, log } from './utils/logger'
import './index.css'

// Global structured logging: window.onerror, unhandledrejection, boot marker.
// Buffered delivery → POST /api/system/client-logs → backend/storage/log/frontend.log
installGlobalErrorLogging()
log.info('main.jsx executing', { href: location.href })

const shareMatch = window.location.pathname.match(/^\/s\/([^/]+)\/?$/)
const root = ReactDOM.createRoot(document.getElementById('root'))

// UI-02: without a boundary, any render error unmounted the entire tree and left
// a blank white screen — no message, no recovery, playback lost.
root.render(
  <React.StrictMode>
    <ErrorBoundary label="приложения">
      {shareMatch ? <SharePage token={shareMatch[1]} /> : <App />}
    </ErrorBoundary>
  </React.StrictMode>
)

// PWA: register service worker for offline shell + cover cache.
// Failures are non-fatal (private mode, unsupported browser, file://).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((reg) => log.info('service worker registered', { scope: reg.scope }))
      .catch((err) => {
        log.debug('service worker registration skipped', { error: err })
      })
  })
}
