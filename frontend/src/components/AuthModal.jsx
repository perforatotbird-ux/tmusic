import React, { useState } from 'react'
import { useAuthStore } from '../store/authStore'
import { Lock, ShieldCheck, Key, AlertCircle } from 'lucide-react'

export function AuthModal() {
  const { isAuthModalOpen, loginWithPin, loginWithPassword, isLoading, error } = useAuthStore()
  const [pin, setPin] = useState('')
  const [mode, setMode] = useState('pin') // 'pin' | 'password'
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')

  if (!isAuthModalOpen) return null

  const handlePinDigit = (digit) => {
    if (pin.length < 8) {
      const nextPin = pin + digit
      setPin(nextPin)
      if (nextPin.length === 4) {
        // Auto-submit 4-digit PIN for convenience
        loginWithPin(nextPin)
      }
    }
  }

  const handlePinBackspace = () => {
    setPin((prev) => prev.slice(0, -1))
  }

  const handlePasswordSubmit = (e) => {
    e.preventDefault()
    if (username && password) {
      loginWithPassword(username, password)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(10, 9, 8, 0.92)',
        backdropFilter: 'blur(10px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        padding: '20px',
      }}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--line-bright)',
          borderRadius: 'var(--radius)',
          maxWidth: '380px',
          width: '100%',
          padding: '30px 24px',
          boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
          textAlign: 'center',
        }}
      >
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

        {mode === 'pin' ? (
          <div>
            {/* PIN Digits Display */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'center',
                gap: '12px',
                marginBottom: '24px',
              }}
            >
              {[0, 1, 2, 3].map((idx) => (
                <div
                  key={idx}
                  style={{
                    width: '18px',
                    height: '18px',
                    borderRadius: '50%',
                    border: '2px solid var(--line-bright)',
                    backgroundColor: pin.length > idx ? 'var(--accent)' : 'transparent',
                    boxShadow: pin.length > idx ? '0 0 10px rgba(212, 255, 63, 0.4)' : 'none',
                    transition: 'all 0.15s ease',
                  }}
                />
              ))}
            </div>

            {/* Numeric Keypad */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '10px',
                maxWidth: '280px',
                margin: '0 auto 20px auto',
              }}
            >
              {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((num) => (
                <button
                  key={num}
                  disabled={isLoading}
                  onClick={() => handlePinDigit(num.toString())}
                  style={{
                    height: '52px',
                    fontSize: '19px',
                    fontWeight: '700',
                    backgroundColor: 'rgba(255, 255, 255, 0.03)',
                    border: '1px solid var(--line)',
                    borderRadius: '10px',
                    color: 'var(--ink)',
                    cursor: 'pointer',
                    transition: 'all 0.1s ease',
                  }}
                  onMouseDown={(e) => (e.currentTarget.style.backgroundColor = 'var(--accent-muted)')}
                  onMouseUp={(e) => (e.currentTarget.style.backgroundColor = 'rgba(255, 255, 255, 0.03)')}
                >
                  {num}
                </button>
              ))}
              <button
                disabled={isLoading}
                onClick={() => setPin('')}
                style={{
                  height: '52px',
                  fontSize: '12px',
                  fontWeight: '700',
                  textTransform: 'uppercase',
                  backgroundColor: 'rgba(255, 255, 255, 0.02)',
                  border: '1px solid var(--line)',
                  borderRadius: '10px',
                  color: 'var(--dim)',
                  cursor: 'pointer',
                }}
              >
                Clear
              </button>
              <button
                disabled={isLoading}
                onClick={() => handlePinDigit('0')}
                style={{
                  height: '52px',
                  fontSize: '19px',
                  fontWeight: '700',
                  backgroundColor: 'rgba(255, 255, 255, 0.03)',
                  border: '1px solid var(--line)',
                  borderRadius: '10px',
                  color: 'var(--ink)',
                  cursor: 'pointer',
                }}
              >
                0
              </button>
              <button
                disabled={isLoading}
                onClick={handlePinBackspace}
                style={{
                  height: '52px',
                  fontSize: '14px',
                  backgroundColor: 'rgba(255, 255, 255, 0.02)',
                  border: '1px solid var(--line)',
                  borderRadius: '10px',
                  color: 'var(--dim)',
                  cursor: 'pointer',
                }}
              >
                ⌫
              </button>
            </div>

            <button
              onClick={() => setMode('password')}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--dim)',
                fontSize: '12.5px',
                cursor: 'pointer',
                textDecoration: 'underline',
              }}
            >
              Войти по логину и паролю
            </button>
          </div>
        ) : (
          <form onSubmit={handlePasswordSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            <input
              type="text"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              style={{
                width: '100%',
                padding: '12px 14px',
                backgroundColor: 'rgba(255, 255, 255, 0.04)',
                border: '1px solid var(--line)',
                borderRadius: '8px',
                color: 'var(--ink)',
                outline: 'none',
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
                backgroundColor: 'rgba(255, 255, 255, 0.04)',
                border: '1px solid var(--line)',
                borderRadius: '8px',
                color: 'var(--ink)',
                outline: 'none',
              }}
            />
            <button
              type="submit"
              disabled={isLoading}
              style={{
                padding: '12px',
                backgroundColor: 'var(--accent)',
                color: '#141210',
                border: 'none',
                borderRadius: '8px',
                fontWeight: '700',
                cursor: 'pointer',
              }}
            >
              {isLoading ? 'Проверка...' : 'Войти'}
            </button>
            <button
              type="button"
              onClick={() => setMode('pin')}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--dim)',
                fontSize: '12.5px',
                cursor: 'pointer',
                textDecoration: 'underline',
              }}
            >
              Вернуться к вводу PIN
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
