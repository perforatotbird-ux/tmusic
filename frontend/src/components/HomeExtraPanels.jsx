import React, { useEffect, useState } from 'react'
import { History, ListMusic, Play, Plus, Trash2 } from 'lucide-react'
import { playbackApi } from '../api/playback'
import { playlistsApi } from '../api/playlists'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'

function formatTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

export function HomeExtraPanels() {
  const { isAuthenticated } = useAuthStore()
  const { playTrack, playQueue } = usePlayerStore()
  const [continueItems, setContinueItems] = useState([])
  const [recentItems, setRecentItems] = useState([])
  const [playlists, setPlaylists] = useState([])
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isAuthenticated) {
      setContinueItems([])
      setRecentItems([])
      setPlaylists([])
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const [pos, hist, pls] = await Promise.all([
          playbackApi.listPositions(12),
          playbackApi.listHistory(12),
          playlistsApi.list(),
        ])
        if (cancelled) return
        const posItems = (pos?.items || []).filter((x) => x.position_sec >= 5)
        setContinueItems(posItems)
        setRecentItems(hist?.items || [])
        setPlaylists(pls || [])
      } catch {
        if (!cancelled) {
          setContinueItems([])
          setRecentItems([])
          setPlaylists([])
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isAuthenticated])

  if (!isAuthenticated) return null

  const asTrack = (row) => ({
    id: row.file_id,
    name: row.file_name || `Трек ${row.file_id}`,
    stream_url: row.stream_url || `/api/stream/${row.file_id}`,
    folder_id: null,
    mime_type: 'audio/mpeg',
    size: null,
  })

  const handlePlayContinue = (row) => {
    const track = asTrack(row)
    playQueue([track], 0, {
      title: track.name,
      artistName: 'Продолжить',
      coverUrl: null,
    })
  }

  const handleCreatePlaylist = async () => {
    const name = newName.trim()
    if (!name || busy) return
    setBusy(true)
    try {
      await playlistsApi.create(name)
      setNewName('')
      const pls = await playlistsApi.list()
      setPlaylists(pls || [])
    } catch (e) {
      console.error(e)
    } finally {
      setBusy(false)
    }
  }

  const handlePlayPlaylist = async (pl) => {
    try {
      const detail = await playlistsApi.get(pl.id)
      const tracks = (detail?.tracks || []).map((t) => ({
        id: t.file_id,
        name: t.file_name || `Трек ${t.file_id}`,
        stream_url: t.stream_url,
        folder_id: null,
        mime_type: t.mime_type || 'audio/mpeg',
        size: t.size ?? null,
      }))
      if (tracks.length) {
        playQueue(tracks, 0, { title: detail.name, artistName: 'Плейлист', coverUrl: null })
      }
    } catch (e) {
      console.error(e)
    }
  }

  const handleDeletePlaylist = async (pl) => {
    try {
      await playlistsApi.remove(pl.id)
      setPlaylists((prev) => prev.filter((x) => x.id !== pl.id))
    } catch (e) {
      console.error(e)
    }
  }

  const stripStyle = {
    display: 'flex',
    gap: '12px',
    overflowX: 'auto',
    paddingBottom: '8px',
    marginBottom: '20px',
  }

  const chipStyle = {
    minWidth: '180px',
    maxWidth: '220px',
    background: 'var(--bg-card, #1c1917)',
    border: '1px solid var(--line, #333)',
    borderRadius: '12px',
    padding: '12px',
    color: 'var(--ink)',
  }

  return (
    <div style={{ marginBottom: '28px' }}>
      {(continueItems.length > 0 || recentItems.length > 0) && (
        <section>
          <h3
            className="title-zine"
            style={{ fontSize: '15px', margin: '0 0 10px', display: 'flex', alignItems: 'center', gap: '8px' }}
          >
            <History size={16} style={{ color: 'var(--accent)' }} />
            {continueItems.length ? 'Продолжить слушать' : 'Недавнее'}
          </h3>
          <div style={stripStyle}>
            {(continueItems.length ? continueItems : recentItems).slice(0, 12).map((row) => {
              const track = asTrack(row)
              return (
                <div key={`${row.file_id}-${row.updated_at || row.started_at || row.id || Math.random()}`} style={chipStyle}>
                  <div
                    style={{
                      fontSize: '13px',
                      fontWeight: 700,
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      marginBottom: '6px',
                    }}
                  >
                    {track.name}
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--dim)', marginBottom: '10px' }}>
                    {typeof row.position_sec === 'number'
                      ? `${formatTime(row.position_sec)} / ${formatTime(row.duration_sec || 0)}`
                      : 'недавно'}
                  </div>
                  <button
                    onClick={() => handlePlayContinue(row)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '6px',
                      background: 'var(--accent)',
                      color: '#141210',
                      border: 'none',
                      borderRadius: '999px',
                      padding: '6px 12px',
                      fontWeight: 800,
                      fontSize: '12px',
                      cursor: 'pointer',
                    }}
                  >
                    <Play size={13} fill="#141210" /> Играть
                  </button>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <section>
        <h3
          className="title-zine"
          style={{ fontSize: '15px', margin: '0 0 10px', display: 'flex', alignItems: 'center', gap: '8px' }}
        >
          <ListMusic size={16} style={{ color: 'var(--accent)' }} />
          Плейлисты
        </h3>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Название плейлиста"
            style={{
              flex: '1 1 180px',
              maxWidth: '280px',
              background: 'var(--bg-card, #1c1917)',
              border: '1px solid var(--line, #333)',
              color: 'var(--ink)',
              borderRadius: '8px',
              padding: '8px 12px',
              fontSize: '13px',
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreatePlaylist()
            }}
          />
          <button
            onClick={handleCreatePlaylist}
            disabled={busy || !newName.trim()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              background: 'var(--accent)',
              color: '#141210',
              border: 'none',
              borderRadius: '8px',
              padding: '8px 14px',
              fontWeight: 800,
              fontSize: '12px',
              cursor: 'pointer',
              opacity: busy || !newName.trim() ? 0.5 : 1,
            }}
          >
            <Plus size={14} /> Создать
          </button>
        </div>
        {playlists.length === 0 ? (
          <div style={{ color: 'var(--dim)', fontSize: '13px' }}>Плейлистов пока нет — создайте первый.</div>
        ) : (
          <div style={stripStyle}>
            {playlists.map((pl) => (
              <div key={pl.id} style={chipStyle}>
                <div style={{ fontSize: '13px', fontWeight: 700, marginBottom: '4px' }}>{pl.name}</div>
                <div style={{ fontSize: '11px', color: 'var(--dim)', marginBottom: '10px' }}>
                  {pl.track_count || 0} трек(ов)
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button
                    onClick={() => handlePlayPlaylist(pl)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: '4px',
                      background: 'var(--accent)',
                      color: '#141210',
                      border: 'none',
                      borderRadius: '999px',
                      padding: '6px 10px',
                      fontWeight: 800,
                      fontSize: '12px',
                      cursor: 'pointer',
                    }}
                  >
                    <Play size={12} fill="#141210" /> Слушать
                  </button>
                  <button
                    onClick={() => handleDeletePlaylist(pl)}
                    title="Удалить"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--line, #333)',
                      color: 'var(--dim)',
                      borderRadius: '999px',
                      padding: '6px 8px',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                    }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
