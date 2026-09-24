import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { installGlobalErrorLogging, log } from './utils/logger'
import './index.css'

// Global structured logging: window.onerror, unhandledrejection, boot marker.
// Buffered delivery → POST /api/system/client-logs → backend/storage/log/frontend.log
installGlobalErrorLogging()
log.info('main.jsx executing', { href: location.href })

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
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
