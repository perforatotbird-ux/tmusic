import React, { useEffect, useRef, useState } from 'react'

export function SharePage({ token }) {
  const audioRef = useRef(null)
  const [share, setShare] = useState(null)
  const [error, setError] = useState(null)
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    let active = true
    fetch(`/api/share/${encodeURIComponent(token)}`)
      .then(async (response) => {
        const data = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(data.detail || 'Ссылка не найдена или истекла')
        if (active) setShare(data)
      })
      .catch((e) => {
        if (active) setError(e.message)
      })
    return () => {
      active = false
    }
  }, [token])

  const toggle = async () => {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      try {
        await audio.play()
      } catch {
        setError('Не удалось начать воспроизведение')
      }
    } else {
      audio.pause()
    }
  }

  if (error) {
    return (
      <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 24, background: 'var(--bg)', color: 'var(--ink)' }}>
        <div style={{ maxWidth: 420, textAlign: 'center' }}>
          <div style={{ fontSize: 42, marginBottom: 12 }}>T-Music</div>
          <h1 style={{ fontSize: 20 }}>Ссылка недоступна</h1>
          <p style={{ color: 'var(--dim)', fontSize: 13 }}>{error}</p>
        </div>
      </div>
    )
  }

  if (!share) {
    return (
      <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', background: 'var(--bg)', color: 'var(--dim)' }}>
        Загрузка ссылки…
      </div>
    )
  }

  return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 24, background: 'var(--bg)', color: 'var(--ink)' }}>
      <div style={{ width: 'min(100%, 520px)', background: 'var(--bg-card)', border: '1px solid var(--line)', borderRadius: 18, padding: 24 }}>
        <div style={{ fontSize: 11, fontWeight: 800, color: 'var(--accent)', letterSpacing: '0.12em' }}>T-MUSIC SHARE</div>
        <h1 style={{ fontSize: 22, margin: '12px 0 6px', wordBreak: 'break-word' }}>{share.track_name}</h1>
        <div style={{ color: 'var(--dim)', fontSize: 12, marginBottom: 20 }}>
          {share.expires_at ? `Ссылка действует до ${new Date(share.expires_at).toLocaleString()}` : 'Бессрочная публичная ссылка'}
        </div>
        <audio
          ref={audioRef}
          src={`/api/share/${encodeURIComponent(token)}/stream`}
          controls
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onError={() => setError('Аудиопоток недоступен')}
          style={{ width: '100%' }}
        />
        <button
          type="button"
          onClick={toggle}
          style={{ marginTop: 16, width: '100%', border: 0, borderRadius: 10, padding: '12px 16px', background: 'var(--accent)', color: 'var(--on-accent)', fontWeight: 800, cursor: 'pointer' }}
        >
          {playing ? 'Пауза' : 'Слушать трек'}
        </button>
      </div>
    </div>
  )
}
