import React, { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock'

/**
 * UI-11 / UI-26: only 3 of 7 overlays had `role="dialog"`, none trapped focus or
 * restored it on close, and none applied safe-area padding (content slid under
 * the notch in landscape despite `viewport-fit=cover`).
 *
 * This is the single shared primitive: dialog semantics, Escape, backdrop
 * click, scroll lock (iOS-correct), focus trap, focus restore and safe-area
 * padding — all opt-out where a flow must be undismissable (mandatory password
 * change).
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function Modal({
  open = true,
  onClose,
  title,
  labelledBy,
  describedBy,
  dismissable = true,
  closeOnBackdrop = true,
  initialFocusRef,
  zIndex = 'var(--z-modal)',
  maxWidth = 560,
  maxHeight = 'min(92dvh, calc(100dvh - 32px))',
  backdrop = 'rgba(10,9,8,.72)',
  backdropFilter,
  align = 'center',
  className,
  style,
  children,
}) {
  const panelRef = useRef(null)
  const restoreRef = useRef(null)
  const closingRef = useRef(false)

  useBodyScrollLock(open)

  // Remember what had focus so we can put it back — no overlay did this before.
  useEffect(() => {
    if (!open) return undefined
    restoreRef.current = document.activeElement

    // Move focus into the dialog: explicit ref wins, else first focusable,
    // else the panel itself.
    const raf = requestAnimationFrame(() => {
      const panel = panelRef.current
      if (!panel) return
      if (dismissable === false && initialFocusRef?.current) {
        initialFocusRef.current.focus?.()
        return
      }
      const target =
        initialFocusRef?.current ||
        panel.querySelector(FOCUSABLE) ||
        panel
      try { target.focus?.({ preventScroll: true }) } catch { /* non-focusable fallback */ }
    })

    return () => {
      cancelAnimationFrame(raf)
      const prev = restoreRef.current
      if (prev && typeof prev.focus === 'function' && document.contains(prev)) {
        try { prev.focus({ preventScroll: true }) } catch { /* element gone */ }
      }
    }
  }, [open, initialFocusRef])

  const requestClose = React.useCallback(() => {
    if (!dismissable || closingRef.current) return
    closingRef.current = true
    onClose?.()
    // Allow a subsequent open in the same tick.
    requestAnimationFrame(() => { closingRef.current = false })
  }, [dismissable, onClose])

  // Escape + Tab trap.
  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (!dismissable) return
        e.stopPropagation()
        requestClose()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const nodes = Array.from(panel.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement
      )
      if (!nodes.length) {
        e.preventDefault()
        panel.focus?.()
        return
      }
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || !panel.contains(active))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && active === last) {
        e.preventDefault()
        first.focus()
      } else if (!panel.contains(active)) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, dismissable, requestClose])

  if (!open) return null

  const onBackdropMouseDown = (e) => {
    // Only a genuine backdrop hit closes — never a click that started inside.
    if (!closeOnBackdrop || !dismissable) return
    if (e.target === e.currentTarget) requestClose()
  }

  const panel = (
    <div
      className={className}
      onMouseDown={onBackdropMouseDown}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex,
        display: 'flex',
        alignItems: align === 'end' ? 'flex-end' : 'center',
        justifyContent: 'center',
        background: backdrop,
        backdropFilter,
        WebkitBackdropFilter: backdropFilter,
        padding:
          'max(16px, var(--safe-top)) max(16px, var(--safe-right)) max(16px, var(--safe-bottom)) max(16px, var(--safe-left))',
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={labelledBy ? undefined : title}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        style={{
          width: '100%',
          maxWidth,
          maxHeight,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: 'var(--bg-card)',
          border: '1px solid var(--line)',
          borderRadius: 14,
          boxShadow: '0 24px 60px rgba(0,0,0,.55)',
          outline: 'none',
          ...style,
        }}
      >
        {children}
      </div>
    </div>
  )

  if (typeof document === 'undefined') return panel
  return createPortal(panel, document.body)
}

export default Modal
