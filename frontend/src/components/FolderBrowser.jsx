/**
 * Windows-style VFS folder browser.
 * Single click on folder → add tracks to player queue + saved playlist → play.
 * Open button / double-click → browse inside the folder.
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft,
  ChevronRight,
  Folder,
  FolderOpen,
  Loader2,
  Music,
  Play,
  HardDrive,
} from 'lucide-react'
import { vfsApi } from '../api/vfs'
import { playlistsApi } from '../api/playlists'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'

const rowStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: '12px',
  padding: '11px 12px',
  borderRadius: '10px',
  cursor: 'pointer',
  border: '1px solid transparent',
  color: 'var(--ink)',
  background: 'transparent',
  width: '100%',
  textAlign: 'left',
  fontSize: '13.5px',
}

export function FolderBrowser() {
  const [stack, setStack] = useState([{ id: null, name: 'Этот компьютер' }])
  const [items, setItems] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [playingId, setPlayingId] = useState(null)
  const { playQueue } = usePlayerStore()
  const { isAuthenticated } = useAuthStore()

  const current = stack[stack.length - 1]

  const loadChildren = useCallback(async (folderId) => {
    setIsLoading(true)
    setError(null)
    try {
      if (folderId == null) {
        const roots = await vfsApi.roots()
        setItems((roots || []).map((f) => ({ type: 'folder', folder: f })))
      } else {
        const data = await vfsApi.children(folderId)
        const folders = (data?.subfolders || []).map((f) => ({ type: 'folder', folder: f }))
        const tracks = (data?.tracks || []).map((t) => ({ type: 'track', track: t }))
        setItems([...folders, ...tracks])
      }
    } catch (e) {
      console.error(e)
      setError('Не удалось открыть папку')
      setItems([])
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadChildren(current.id)
  }, [current.id, loadChildren])

  const goUp = () => {
    if (stack.length <= 1) return
    setStack(stack.slice(0, -1))
  }

  const openFolder = (folder) => {
    setStack([...stack, { id: folder.id, name: folder.name }])
  }

  /** Click folder → playlist + play (as requested). */
  const playFolder = async (folder) => {
    if (playingId) return
    setPlayingId(folder.id)
    try {
      const tracks = await vfsApi.tracks(folder.id, true)
      if (!tracks || tracks.length === 0) {
        openFolder(folder)
        return
      }
      playQueue(
        tracks,
        0,
        {
          title: folder.name,
          artistName: 'Папка',
          coverUrl: null,
        }
      )

      if (isAuthenticated) {
        try {
          const lists = await playlistsApi.list()
          const existing = (lists || []).find(
            (p) => (p.name || '').toLowerCase() === (folder.name || '').toLowerCase()
          )
          const ids = tracks.slice(0, 500).map((t) => t.id)
          if (existing) {
            const detail = await playlistsApi.get(existing.id)
            for (const row of detail?.tracks || []) {
              try {
                await playlistsApi.removeItem(existing.id, row.id)
              } catch {
                /* ignore */
              }
            }
            for (const fileId of ids) {
              try {
                await playlistsApi.addTrack(existing.id, fileId)
              } catch {
                /* ignore */
              }
            }
          } else {
            const created = await playlistsApi.create(folder.name)
            for (const fileId of ids) {
              try {
                await playlistsApi.addTrack(created.id, fileId)
              } catch {
                /* ignore */
              }
            }
          }
        } catch (e) {
          console.error('Playlist mirror failed:', e)
        }
      }
    } catch (e) {
      console.error(e)
    } finally {
      setPlayingId(null)
    }
  }

  return (
    <div className="tmusic-folder-browser">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          marginBottom: '12px',
          flexWrap: 'wrap',
        }}
      >
        <button
          onClick={goUp}
          disabled={stack.length <= 1}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '6px',
            background: stack.length <= 1 ? 'var(--line)' : 'var(--bg-card)',
            color: stack.length <= 1 ? 'var(--dim)' : 'var(--ink)',
            border: '1px solid var(--line)',
            borderRadius: '8px',
            padding: '6px 10px',
            fontSize: '12px',
            fontWeight: 700,
            cursor: stack.length <= 1 ? 'default' : 'pointer',
          }}
        >
          <ArrowLeft size={14} /> Вверх
        </button>
        <div
          style={{
            fontSize: '12px',
            color: 'var(--dim)',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            flexWrap: 'wrap',
            minWidth: 0,
          }}
        >
          <HardDrive size={14} style={{ color: 'var(--accent)' }} />
          {stack.map((s, i) => (
            <React.Fragment key={`${s.id ?? 'root'}-${i}`}>
              {i > 0 && <ChevronRight size={12} style={{ opacity: 0.5 }} />}
              <button
                onClick={() => setStack(stack.slice(0, i + 1))}
                style={{
                  background: 'none',
                  border: 'none',
                  color: i === stack.length - 1 ? 'var(--ink)' : 'var(--dim)',
                  fontWeight: i === stack.length - 1 ? 700 : 500,
                  cursor: 'pointer',
                  fontSize: '12px',
                  padding: 0,
                }}
              >
                {s.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      </div>

      {isLoading && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '12px',
            padding: '48px 16px',
            color: 'var(--dim)',
          }}
        >
          <Loader2 size={28} className="animate-spin" style={{ color: 'var(--accent)' }} />
          <div style={{ fontSize: '13px' }}>Открытие папки…</div>
        </div>
      )}

      {!isLoading && error && (
        <div style={{ padding: '24px', color: 'var(--danger, #f66)', fontSize: '13px' }}>{error}</div>
      )}

      {!isLoading && !error && items.length === 0 && (
        <div
          style={{
            padding: '40px 16px',
            textAlign: 'center',
            color: 'var(--dim)',
            fontSize: '13px',
          }}
        >
          <Folder size={32} style={{ opacity: 0.35, marginBottom: '8px' }} />
          <div>Папка пуста</div>
        </div>
      )}

      {!isLoading && !error && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
          {items.map((item) => {
            if (item.type === 'folder') {
              const f = item.folder
              const busy = playingId === f.id
              return (
                <div
                  key={`f-${f.id}`}
                  className="tmusic-track-row"
                  style={{
                    ...rowStyle,
                    background: busy ? 'var(--accent-muted)' : undefined,
                  }}
                  onClick={() => playFolder(f)}
                  title="Клик — в плейлист и воспроизведение"
                >
                  <FolderOpen size={18} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontWeight: 700,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {f.name}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--dim)' }}>
                      {f.subfolder_count > 0 && `${f.subfolder_count} папок · `}
                      {f.recursive_track_count || f.track_count || 0} трек(ов)
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      openFolder(f)
                    }}
                    title="Открыть папку (иерархия)"
                    style={{
                      width: '36px',
                      height: '36px',
                      borderRadius: '8px',
                      border: '1px solid var(--line)',
                      background: 'transparent',
                      color: 'var(--ink)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                      flexShrink: 0,
                    }}
                  >
                    <ChevronRight size={16} />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      playFolder(f)
                    }}
                    title="В плейлист + играть"
                    style={{
                      width: '36px',
                      height: '36px',
                      borderRadius: '50%',
                      border: 'none',
                      background: 'var(--accent)',
                      color: '#141210',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      cursor: 'pointer',
                      flexShrink: 0,
                    }}
                  >
                    {busy ? (
                      <Loader2 size={16} className="animate-spin" />
                    ) : (
                      <Play size={15} fill="#141210" />
                    )}
                  </button>
                </div>
              )
            }

            const t = item.track
            return (
              <div
                key={`t-${t.id}`}
                className="tmusic-track-row"
                style={{ ...rowStyle, cursor: 'default' }}
              >
                <Music size={16} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                <div
                  style={{
                    flex: 1,
                    minWidth: 0,
                    fontSize: '13px',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    color: 'var(--ink)',
                  }}
                >
                  {(t.name || '').replace(/\.[^/.]+$/, '')}
                </div>
                <button
                  onClick={() =>
                    playQueue([t], 0, {
                      title: (t.name || '').replace(/\.[^/.]+$/, ''),
                      artistName: 'Файл',
                      coverUrl: null,
                    })
                  }
                  title="Играть трек"
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    border: '1px solid var(--line)',
                    background: 'transparent',
                    color: 'var(--accent)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    flexShrink: 0,
                  }}
                >
                  <Play size={13} fill="var(--accent)" />
                </button>
              </div>
            )
          })}
        </div>
      )}

      <div
        style={{
          marginTop: '14px',
          fontSize: '11px',
          color: 'var(--dim)',
          lineHeight: 1.5,
        }}
      >
        Клик по папке — добавить все треки в плейлист и воспроизвести. Кнопка › — открыть папку как
        в Проводнике.
      </div>
    </div>
  )
}
