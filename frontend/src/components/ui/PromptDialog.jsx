import React, { useEffect, useRef, useState } from 'react'
import Modal from './Modal'

/**
 * UI-38: accessible replacement for window.prompt. Native prompt() is unstyled,
 * blocks the thread and (crucially) tears the parent modal's focus trap. This
 * dialog lives inside the same Modal primitive, so focus stays contained.
 * Use through the usePrompt() hook.
 */
export function PromptDialog({
  open,
  title,
  message,
  inputLabel,
  placeholder = '',
  defaultValue = '',
  type = 'text',
  confirmLabel = 'Сохранить',
  cancelLabel = 'Отмена',
  onConfirm,
  onCancel,
}) {
  const [value, setValue] = useState(defaultValue)
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) setValue(defaultValue)
  }, [open, defaultValue])

  const submit = (e) => {
    e?.preventDefault()
    onConfirm(value)
  }

  return (
    <Modal open={open} onClose={onCancel} title={title} maxWidth={420} initialFocusRef={inputRef}>
      <form onSubmit={submit} style={{ padding: 24 }}>
        {message && (
          <p style={{ color: 'var(--dim)', fontSize: 13.5, marginBottom: 14, lineHeight: 1.5 }}>{message}</p>
        )}
        <label
          htmlFor="tm-prompt-input"
          style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--dim)', marginBottom: 6 }}
        >
          {inputLabel || title}
        </label>
        <input
          id="tm-prompt-input"
          ref={inputRef}
          type={type}
          value={value}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          style={{
            width: '100%',
            padding: '10px 12px',
            borderRadius: 8,
            border: '1px solid var(--line-bright)',
            background: 'var(--bg)',
            color: 'var(--ink)',
            fontSize: 14,
          }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 20 }}>
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
            type="submit"
            style={{
              padding: '10px 16px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--accent)',
              color: 'var(--on-accent)',
              cursor: 'pointer',
              fontWeight: 700,
              fontSize: 14,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default PromptDialog
