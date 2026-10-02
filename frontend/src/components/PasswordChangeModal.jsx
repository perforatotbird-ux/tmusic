import React, { useState } from 'react'
import { authApi } from '../api/auth'
import { useAuthStore } from '../store/authStore'
import Modal from './ui/Modal'

export function PasswordChangeModal({ required = false, onClose }) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const logout = useAuthStore((state) => state.logout)

  const submit = async (event) => {
    event.preventDefault()
    if (newPassword !== confirmation) {
      setError('Новые пароли не совпадают.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmation('')
      // Password change revokes every previous JWT. Re-login with the full
      // Argon2id password instead of retaining the now-invalid session cookie.
      await logout()
      onClose?.()
    } catch (err) {
      setError(err.message || 'Не удалось изменить пароль.')
    } finally {
      setBusy(false)
    }
  }

  const inputStyle = {
    padding: 12, borderRadius: 8, border: '1px solid var(--line)',
    background: 'var(--bg)', color: 'var(--ink)', fontSize: 16,
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Сменить пароль"
      labelledBy="password-change-title"
      maxWidth={430}
      zIndex="var(--z-password)"
      dismissable={!required}
      backdrop="rgba(10,9,8,.94)"
      style={{ background: 'transparent', border: 'none', boxShadow: 'none' }}
    >
      <section style={{ width: '100%', maxWidth: 430, padding: 24, borderRadius: 12, border: '1px solid var(--line-bright)', background: 'var(--bg-card)' }}>
        <h2 id="password-change-title">Сменить пароль</h2>
        <p style={{ color: 'var(--dim)', fontSize: 14 }}>
          {required
            ? 'Старый bcrypt-пароль достигал лимита 72 байта. Задайте полный новый пароль для безопасного перехода на Argon2id. До этого доступ к библиотеке ограничен.'
            : 'Пароль будет сохранён с Argon2id. После изменения потребуется войти заново на всех устройствах.'}
        </p>
        {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <label htmlFor="current-password">Текущий пароль</label>
          <input id="current-password" type="password" autoComplete="current-password" autoFocus
            value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)}
            maxLength={1024} required disabled={busy} style={inputStyle} />
          <label htmlFor="new-password">Новый пароль (8–128 символов)</label>
          <input id="new-password" type="password" autoComplete="new-password"
            value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
            minLength={8} maxLength={128} required disabled={busy} style={inputStyle} />
          <label htmlFor="confirm-password">Повторите новый пароль</label>
          <input id="confirm-password" type="password" autoComplete="new-password"
            value={confirmation} onChange={(e) => setConfirmation(e.target.value)}
            minLength={8} maxLength={128} required disabled={busy} style={inputStyle} />
          <button type="submit" disabled={busy} style={{ ...inputStyle, background: 'var(--accent)', color: 'var(--on-accent)' }}>
            {busy ? 'Сохранение…' : 'Сохранить и войти заново'}
          </button>
          {!required && <button type="button" onClick={onClose} disabled={busy}>Отмена</button>}
          {required && <button type="button" onClick={async () => { await logout(); onClose?.() }} disabled={busy}>Выйти</button>}
        </form>
      </section>
    </Modal>
  )
}
