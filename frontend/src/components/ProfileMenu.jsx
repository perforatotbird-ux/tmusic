/**
 * T-Music ProfileMenu — active-profile circle in the top-right corner.
 * Shows avatar (or letter badge), role, and a dropdown with:
 * avatar upload (file or URL), admin panel entry, logout/login.
 */
import React, { useEffect, useRef, useState } from 'react'
import {
  ShieldCheck,
  Pencil,
  LogOut,
  LogIn,
  Image as ImageIcon,
  Link as LinkIcon,
  Trash2,
  X,
  Check,
  Loader2,
  User as UserIcon,
  Wifi,
} from 'lucide-react'
import { authApi } from '../api/auth'
import { IS_NATIVE, openServerDiscovery } from '../services/nativeBridge'

export const ROLE_LABEL = { admin: 'Администратор', editor: 'Редактор', user: 'Слушатель' }

export function roleLabel(user) {
  if (!user) return 'Гость'
  if (user.username === 'admin' && user.role !== 'admin') return 'Администратор'
  return ROLE_LABEL[user.role] || 'Слушатель'
}

export function ProfileCircle({ user, size = 40, onClick, title }) {
  const letter = ((user?.username || '?').trim()[0] || '?').toUpperCase()
  return (
    <button
      type="button"
      onClick={onClick}
      title={title || (user ? `${user.username} • ${roleLabel(user)}` : 'Профиль')}
      aria-label={user ? `Профиль ${user.username}` : 'Войти'}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        border: user ? '2px solid var(--accent)' : '1px solid var(--line-bright)',
        background: 'var(--bg-card)',
        cursor: 'pointer',
        padding: 0,
        overflow: 'hidden',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: user ? '0 0 0 2px var(--accent-muted)' : 'none',
      }}
    >
      {user?.avatar_url ? (
        <img
          src={user.avatar_url}
          alt={user.username}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      ) : user ? (
        <span style={{ fontWeight: 900, fontSize: size * 0.45, color: 'var(--accent)' }}>{letter}</span>
      ) : (
        <UserIcon size={size * 0.5} style={{ color: 'var(--dim)' }} />
      )}
    </button>
  )
}

const menuBtn = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  width: '100%',
  padding: '10px 12px',
  minHeight: 44,
  background: 'transparent',
  border: 'none',
  borderRadius: 8,
  color: 'var(--ink)',
  fontSize: 13.5,
  fontWeight: 600,
  cursor: 'pointer',
  textAlign: 'left',
}

export function ProfileMenu({ user, isAdmin, onLogin, onLogout, onOpenAdmin, onProfileChanged }) {
  const [open, setOpen] = useState(false)
  const [avatarMode, setAvatarMode] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const wrapRef = useRef(null)
  const fileRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setOpen(false)
        setAvatarMode(false)
      }
    }
    const onClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false)
        setAvatarMode(false)
      }
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [open ])

  const close = () => {
    setOpen(false)
    setAvatarMode(false)
    setError(null)
    setUrl('')
  }

  const afterChange = async () => {
    await onProfileChanged?.()
    close()
  }

  const handleFile = async (e) => {
    const f = e.target.files?.[0]
    if (!f) return
    setBusy(true)
    setError(null)
    try {
      await authApi.uploadAvatar(f)
      await afterChange()
    } catch (err) {
      setError(err.message || 'Не удалось загрузить аватар')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleUrl = async () => {
    if (!url.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await authApi.setAvatarUrl(url.trim())
      await afterChange()
    } catch (err) {
      setError(err.message || 'Не удалось загрузить аватар по ссылке')
    } finally {
      setBusy(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('Убрать аватар?')) return
    setBusy(true)
    try {
      await authApi.resetAvatar()
      await afterChange()
    } catch (err) {
      setError(err.message || 'Не удалось убрать аватар')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <ProfileCircle user={user} onClick={() => (user ? setOpen((o) => !o) : onLogin?.())} />

      {open && user && (
        <div
          role="menu"
          style={{
            position: 'absolute',
            top: 'calc(100% + 10px)',
            right: 0,
            width: 280,
            background: 'var(--bg-card)',
            border: '1px solid var(--line-bright)',
            borderRadius: 14,
            boxShadow: '0 18px 50px rgba(0,0,0,0.6)',
            padding: 12,
            zIndex: 8000,
          }}
        >
          {/* Identity */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 4px 10px 4px' }}>
            <ProfileCircle user={user} size={46} onClick={() => {}} title={user.username} />
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontWeight: 800,
                  fontSize: 14,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {user.username}
              </div>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: isAdmin ? 'var(--accent)' : 'var(--dim)',
                  background: isAdmin ? 'var(--accent-muted)' : 'rgba(255,255,255,0.05)',
                  borderRadius: 999,
                  padding: '3px 10px',
                  marginTop: 4,
                }}
              >
                <ShieldCheck size={12} /> {roleLabel(user)}
              </div>
            </div>
          </div>

          {error && (
            <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 8 }}>{error}</div>
          )}

          {!avatarMode ? (
            <>
              <button type="button" onClick={() => setAvatarMode(true)} style={menuBtn}>
                <ImageIcon size={15} style={{ color: 'var(--dim)' }} /> Сменить аватар
              </button>
              {isAdmin && (
                <button
                  type="button"
                  onClick={() => {
                    close()
                    onOpenAdmin?.()
                  }}
                  style={menuBtn}
                >
                  <ShieldCheck size={15} style={{ color: 'var(--accent)' }} /> Админка
                </button>
              )}
              {IS_NATIVE && (
                <button
                  type="button"
                  onClick={() => {
                    close()
                    openServerDiscovery()
                  }}
                  style={menuBtn}
                >
                  <Wifi size={15} style={{ color: 'var(--accent)' }} /> Сменить сервер (Wi-Fi)
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  close()
                  onLogout?.()
                }}
                style={{ ...menuBtn, color: 'var(--danger)' }}
              >
                <LogOut size={15} /> Выйти
              </button>
            </>
          ) : (
            <div>
              <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 8 }}>Аватар профиля</div>
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={handleFile}
                style={{ display: 'none' }}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
                style={{ ...menuBtn, border: '1px dashed var(--line-bright)', justifyContent: 'center' }}
              >
                {busy ? <Loader2 size={15} className="animate-spin" /> : <Pencil size={15} />} Загрузить файл
              </button>
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', paddingLeft: 8, color: 'var(--dim)' }}>
                  <LinkIcon size={14} />
                </div>
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleUrl()
                  }}
                  placeholder="https://…/avatar.jpg"
                  style={{
                    flex: 1,
                    minWidth: 0,
                    background: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--ink)',
                    borderRadius: 8,
                    padding: '8px 10px',
                    minHeight: 40,
                    fontSize: 13,
                  }}
                />
                <button
                  type="button"
                  disabled={busy || !url.trim()}
                  onClick={handleUrl}
                  style={{
                    background: 'var(--accent)',
                    color: '#141210',
                    border: 'none',
                    borderRadius: 8,
                    minWidth: 44,
                    minHeight: 40,
                    cursor: busy || !url.trim() ? 'default' : 'pointer',
                    opacity: busy || !url.trim() ? 0.5 : 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                  aria-label="Сохранить аватар по ссылке"
                >
                  <Check size={16} />
                </button>
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                {user.avatar_url && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={handleReset}
                    style={{ ...menuBtn, width: 'auto', color: 'var(--danger)', fontSize: 12 }}
                  >
                    <Trash2 size={14} /> Убрать
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setAvatarMode(false)
                    setError(null)
                    setUrl('')
                  }}
                  style={{ ...menuBtn, width: 'auto', marginLeft: 'auto', fontSize: 12 }}
                >
                  <X size={14} /> Назад
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {open && !user && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 10px)',
            right: 0,
            width: 220,
            background: 'var(--bg-card)',
            border: '1px solid var(--line-bright)',
            borderRadius: 14,
            boxShadow: '0 18px 50px rgba(0,0,0,0.6)',
            padding: 12,
            zIndex: 8000,
          }}
        >
          <button
            type="button"
            onClick={() => {
              close()
              onLogin?.()
            }}
            style={{
              ...menuBtn,
              background: 'var(--accent)',
              color: '#141210',
              justifyContent: 'center',
              fontWeight: 800,
            }}
          >
            <LogIn size={15} /> Войти в T-Music
          </button>
        </div>
      )}
    </div>
  )
}
