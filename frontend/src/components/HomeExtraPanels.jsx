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

const chipBtn = {
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
}

/**
 * Compact panels for the library top bar: 'recent' | 'playlists'.
 */
export function LibraryPanel({ section, onRefreshKey }) {
  const { isAuthenticated } = useAuthStore()
  const { playQueue } = usePlayerStore()
  const [continueItems, setContinueItems] = useState([])
  const [recentItems, setRecentItems] = useState([])
  const [playlists, setPlaylists] = useState([])
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!isAuthenticated || !section) {
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const [pos, hist, pls] = await Promise.all([
          playbackApi.listPositions(16),
          playbackApi.listHistory(16),
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
  }, [isAuthenticated, section, onRefreshKey])

  if (!isAuthenticated || !section) return null

  const asTrack = (row) => ({
    id: row.file_id,
    name: row.file_name || `Трек ${row.file_id}`,
    stream_url: row.stream_url || `/api/stream/${row.file_id}`,
    folder_id: null,
    mime_type: 'audio/mpeg',
    size: null,
  })

  const playRow = (row) => {
    const track = asTrack(row)
    playQueue([track], 0, {
      title: track.name,
      artistName: 'Недавнее',
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
      setPlaylists(await playlistsApi.list())
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

  const rows = continueItems.length ? continueItems : recentItems

  return (
    <div className="tmusic-library-panel">
      {section === 'recent' && (
        <section>
          <div className="tmusic-panel-head">
            <History size={15} />
            <span>Недавнее</span>
            <span className="tmusic-panel-count">{rows.length}</span>
          </div>
          {rows.length === 0 ? (
            <div className="tmusic-panel-empty">Пока ничего не воспроизводилось</div>
          ) : (
            <div className="tmusic-panel-list">
              {rows.slice(0, 20).map((row, idx) => {
                const track = asTrack(row)
                return (
                  <div
                    key={`${row.file_id}-${row.updated_at || row.started_at || idx}`}
                    className="tmusic-panel-row"
                    onClick={() => playRow(row)}
                  >
                    <Play size={13} fill="var(--accent)" />
                    <div className="tmusic-panel-row-main">
                      <div className="tmusic-panel-row-title">{track.name}</div>
                      <div className="tmusic-panel-row-sub">
                        {typeof row.position_sec === 'number'
                          ? `${formatTime(row.position_sec)}${
                              row.duration_sec ? ' / ' + formatTime(row.duration_sec) : ''
                            }`
                          : 'недавно'}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      )}

      {section === 'playlists' && (
        <section>
          <div className="tmusic-panel-head">
            <ListMusic size={15} />
            <span>Плейлисты</span>
            <span className="tmusic-panel-count">{playlists.length}</span>
          </div>
          <div className="tmusic-panel-create">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Новый плейлист"
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreatePlaylist()
              }}
            />
            <button onClick={handleCreatePlaylist} disabled={busy || !newName.trim()} style={chipBtn}>
              <Plus size={13} /> Создать
            </button>
          </div>
          {playlists.length === 0 ? (
            <div className="tmusic-panel-empty">Плейлистов нет — создайте первый</div>
          ) : (
            <div className="tmusic-panel-list">
              {playlists.map((pl) => (
                <div key={pl.id} className="tmusic-panel-row">
                  <button
                    className="tmusic-panel-row-main"
                    style={{ background: 'none', border: 'none', color: 'inherit', textAlign: 'left', cursor: 'pointer', flex: 1 }}
                    onClick={() => handlePlayPlaylist(pl)}
                  >
                    <div className="tmusic-panel-row-title">{pl.name}</div>
                    <div className="tmusic-panel-row-sub">{pl.track_count || 0} трек(ов)</div>
                  </button>
                  <button style={chipBtn} onClick={() => handlePlayPlaylist(pl)}>
                    <Play size={12} fill="#141210" /> Слушать
                  </button>
                  <button
                    className="tmusic-panel-icon-btn"
                    onClick={() => handleDeletePlaylist(pl)}
                    title="Удалить"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

/** @deprecated use LibraryPanel */
export function HomeExtraPanels() {
  return null
}
