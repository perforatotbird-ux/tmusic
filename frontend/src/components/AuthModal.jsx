import React, { useState } from 'react'
import { useAuthStore } from '../store/authStore'
import { Lock, AlertCircle, X } from 'lucide-react'
import Modal from './ui/Modal'

export function AuthModal() {
  const { isAuthModalOpen, loginWithPassword, isLoading, error, setAuthModalOpen, logoutPending, logoutBusy, logout } = useAuthStore()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')

  const handlePasswordSubmit = (e) => {
    e.preventDefault()
    if (username && password) {
      loginWithPassword(username, password)
    }
  }

  return (
    <Modal
      open={isAuthModalOpen}
      onClose={() => setAuthModalOpen(false)}
      title="T-MUSIC ACCESS"
      maxWidth={380}
      backdrop="rgba(10, 9, 8, 0.92)"
      backdropFilter="blur(10px)"
      style={{ border: '1px solid var(--line-bright)', borderRadius: 'var(--radius)' }}
    >
      <div style={{ padding: '28px 22px', textAlign: 'center', position: 'relative' }}>
        <button
          onClick={() => setAuthModalOpen(false)}
          disabled={logoutPending || logoutBusy}
          aria-label="Закрыть"
          style={{
            position: 'absolute',
            top: '12px',
            right: '12px',
            background: 'none',
            border: 'none',
            color: 'var(--dim)',
            cursor: 'pointer',
            minWidth: '40px',
            minHeight: '40px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <X size={18} />
        </button>
        <div
          style={{
            width: '54px',
            height: '54px',
            borderRadius: '50%',
            backgroundColor: 'var(--accent-muted)',
            border: '1px solid rgba(212, 255, 63, 0.3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            color: 'var(--accent)',
          }}
        >
          <Lock size={26} />
        </div>

        <h2 className="title-zine" style={{ fontSize: '22px', marginBottom: '6px' }}>
          T-MUSIC ACCESS
        </h2>
        <p style={{ color: 'var(--dim)', fontSize: '13px', marginBottom: '20px' }}>
          Защищённый вход в персональное аудиооблако
        </p>

        {error && (
          <div
            role="alert"
            style={{
              backgroundColor: 'rgba(255, 107, 107, 0.1)',
              border: '1px solid var(--danger)',
              borderRadius: '8px',
              padding: '10px 14px',
              marginBottom: '18px',
              fontSize: '12.5px',
              color: 'var(--danger)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              textAlign: 'left',
            }}
          >
            <AlertCircle size={16} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}

        {logoutPending && <button type="button" onClick={() => logout()} disabled={logoutBusy}
          style={{ marginBottom: 14, padding: 12 }}>
          {logoutBusy ? 'Повторяем выход…' : 'Повторить выход'}
        </button>}
        <form onSubmit={handlePasswordSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <input
            type="text"
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{
              width: '100%',
              padding: '12px 14px',
              minHeight: '44px',
              fontSize: '16px',
              backgroundColor: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid var(--line)',
              borderRadius: '8px',
              color: 'var(--ink)',
            }}
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{
              width: '100%',
              padding: '12px 14px',
              minHeight: '44px',
              fontSize: '16px',
              backgroundColor: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid var(--line)',
              borderRadius: '8px',
              color: 'var(--ink)',
            }}
          />
          <button
            type="submit"
            disabled={isLoading || logoutPending || logoutBusy}
            style={{
              padding: '12px',
              minHeight: '44px',
              fontSize: '14px',
              backgroundColor: 'var(--accent)',
              color: 'var(--on-accent)',
              border: 'none',
              borderRadius: '8px',
              fontWeight: '700',
              cursor: 'pointer',
            }}
          >
            {isLoading ? 'Проверка...' : 'Войти'}
          </button>
        </form>
      </div>
    </Modal>
  )
}
