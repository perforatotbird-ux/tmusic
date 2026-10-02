import React from 'react'
import Modal from './Modal'

/**
 * UI-15: replaces native window.confirm for destructive actions. Renders an
 * accessible dialog (focus trap, Escape, backdrop, scroll lock via Modal) with
 * explicit confirm/cancel. Use through the useConfirm() hook.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'OK',
  cancelLabel = 'Отмена',
  danger = false,
  onConfirm,
  onCancel,
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      maxWidth={420}
      labelledBy={undefined}
      style={{ background: 'transparent', border: 'none', boxShadow: 'none' }}
    >
      <div style={{ padding: 24 }}>
        <p style={{ color: 'var(--ink)', fontSize: 14, marginBottom: 20, lineHeight: 1.5 }}>{message}</p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              padding: '10px 16px',
              borderRadius: 8,
              border: '1px solid var(--line)',
              background: 'transparent',
              color: 'var(--ink)',
              cursor: 'pointer',
              fontSize: 14,
            }}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            style={{
              padding: '10px 16px',
              borderRadius: 8,
              border: 'none',
              background: danger ? 'var(--danger)' : 'var(--accent)',
              color: danger ? '#fff' : 'var(--on-accent)',
              cursor: 'pointer',
              fontWeight: 700,
              fontSize: 14,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  )
}

export default ConfirmDialog
