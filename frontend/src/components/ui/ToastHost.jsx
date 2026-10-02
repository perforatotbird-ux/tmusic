import React, { useEffect } from 'react'
import { X, Play } from 'lucide-react'
import { usePlayerStore } from '../../store/playerStore'

const AUTO_DISMISS_MS = 6000

/**
 * UI-12 + UI-22: toasts were rendered inline inside the player with a hardcoded
 * `bottom`, which put them *under* the mobile player bar (toast 8000 vs player
 * 8400) and fighting the favourites alert for the same strip of screen. This
 * host owns the stacking layer, offsets each toast by its index, and sits above
 * the player bar via `--z-toast`.
 */
export function ToastHost() {
  const notices = usePlayerStore((s) => s.notices)
  const dismissNotice = usePlayerStore((s) => s.dismissNotice)

  // Per-toast auto-dismiss — previously the whole stack shared one timer keyed
  // on a single notice object, so a newer message cancelled the older timer.
  useEffect(() => {
    if (!notices.length) return undefined
    const timers = notices.map((n) =>
      setTimeout(() => dismissNotice(n.id), AUTO_DISMISS_MS)
    )
    return () => timers.forEach(clearTimeout)
  }, [notices, dismissNotice])

  if (!notices.length) return null

  return (
    <div
      // aria-live on a stable wrapper: screen readers announce additions.
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        left: 0,
        right: 0,
        bottom: 'calc(var(--player-bar-h) + var(--bottom-nav-h) + var(--safe-bottom) + 12px)',
        zIndex: 'var(--z-toast)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 8,
        padding: '0 12px',
        pointerEvents: 'none',
      }}
    >
      {notices.map((n, i) => (
        <div
          key={n.id}
          className={`tmusic-toast ${n.kind === 'error' ? 'tmusic-toast-error' : ''}`}
          // Stack upward: each newer toast sits above the previous one.
          style={{ position: 'relative', bottom: 'auto', left: 'auto', transform: 'none', pointerEvents: 'auto' }}
          data-index={i}
        >
          {n.kind === 'error'
            ? <X size={14} style={{ flexShrink: 0 }} />
            : <Play size={13} style={{ flexShrink: 0 }} />}
          <span>{n.text}</span>
          <button
            type="button"
            className="tmusic-toast-close"
            aria-label="Закрыть уведомление"
            onClick={() => dismissNotice(n.id)}
            style={{ minWidth: 24, minHeight: 24, alignItems: 'center', justifyContent: 'center' }}
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  )
}

export default ToastHost

/**
 * UI-12: the favourites error previously used a hardcoded `bottom: 100` and
 * `zIndex: 9998`, so on mobile it sat on top of the player bar and fought the
 * toasts for the same strip. This keeps it on the same stacking layer with the
 * correct offset above the player + bottom nav.
 */
export function FavoriteError({ message, onRetry, onClose }) {
  if (!message) return null
  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        left: 0,
        right: 0,
        bottom: 'calc(var(--player-bar-h) + var(--bottom-nav-h) + var(--safe-bottom) + 12px)',
        zIndex: 'var(--z-toast)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 8,
        padding: '0 12px',
        pointerEvents: 'none',
      }}
    >
      <div
        className="tmusic-toast tmusic-toast-error"
        style={{
          position: 'relative',
          bottom: 'auto',
          left: 'auto',
          transform: 'none',
          maxWidth: '92vw',
          pointerEvents: 'auto',
          flexWrap: 'wrap',
        }}
      >
        <span>{message}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            style={{ minHeight: 32, alignSelf: 'center', padding: '4px 10px', borderRadius: 6, border: '1px solid var(--line-bright)', background: 'transparent', color: 'var(--danger)', fontWeight: 700, cursor: 'pointer' }}
          >
            Обновить избранное
          </button>
        )}
        {onClose && (
          <button
            type="button"
            aria-label="Закрыть"
            onClick={onClose}
            className="tmusic-toast-close"
            style={{ minWidth: 24, minHeight: 24, alignItems: 'center', justifyContent: 'center' }}
          >
            <X size={13} />
          </button>
        )}
      </div>
    </div>
  )
}
