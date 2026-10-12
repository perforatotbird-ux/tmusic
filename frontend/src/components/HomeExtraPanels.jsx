import React, { useEffect, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  GripVertical,
  History,
  ListMusic,
  Play,
  Plus,
  Trash2,
  ListStart,
  ListEnd,
  Check,
  HardDrive,
} from 'lucide-react'
import { playbackApi } from '../api/playback'
import { playlistsApi } from '../api/playlists'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'
import { useOfflineStore } from '../store/offlineStore'
import { ClickableRow } from './ui/ClickableRow'

function formatTime(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

function stripExt(name) {
  return (name || '').replace(/\.[^/.]+$/, '')
}

const chipBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '6px',
  background: 'var(--accent)',
  color: 'var(--on-accent)',
  border: 'none',
  borderRadius: '999px',
  padding: '6px 12px',
  fontWeight: 800,
  fontSize: '12px',
  cursor: 'pointer',
}

const iconBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '30px',
  height: '30px',
  borderRadius: '8px',
  border: '1px solid var(--line)',
  background: 'transparent',
  color: 'var(--dim)',
  cursor: 'pointer',
  flexShrink: 0,
  padding: 0,
}

/**
 * Compact panels for the library top bar: 'recent' | 'playlists'.
 * «Любимые» здесь больше не живут — они стали карточкой-сборником в каталоге.
 */
export function LibraryPanel({ section, onRefreshKey }) {
  const { isAuthenticated } = useAuthStore()
  const { playQueue, playNext, addToQueue, setNotice } = usePlayerStore()
  const cachedTrackIds = useOfflineStore((s) => s.cachedTrackIds)
  const [continueItems, setContinueItems] = useState([])
  const [recentItems, setRecentItems] = useState([])
  const [playlists, setPlaylists] = useState([])
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [panelError, setPanelError] = useState(null)
  const [refreshTick, setRefreshTick] = useState(0)

  // Playlist detail (reorder)
  const [openPlId, setOpenPlId] = useState(null)
  const [plTracks, setPlTracks] = useState([])
  const [plLoading, setPlLoading] = useState(false)
  const [dragIdx, setDragIdx] = useState(null)
  const [reorderBusy, setReorderBusy] = useState(false)
  // UI-21: guard the playlist play button so a double-tap can't fetch+enqueue twice.
  const [playBusyId, setPlayBusyId] = useState(null)
  const plTracksRef = useRef([])
  const dragActiveRef = useRef(false)

  useEffect(() => {
    plTracksRef.current = plTracks
  }, [plTracks])

  useEffect(() => {
    if (!isAuthenticated || !section) {
      return
    }
    let cancelled = false
    ;(async () => {
      setPanelError(null)
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
        if (!cancelled) setPanelError('Не удалось обновить данные. Показанные данные могут быть устаревшими.')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isAuthenticated, section, onRefreshKey, refreshTick])

  useEffect(() => {
    if (section !== 'playlists') {
      setOpenPlId(null)
      setPlTracks([])
    }
  }, [section])

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

  const queueRow = (row, mode) => {
    const track = asTrack(row)
    if (mode === 'next') playNext(track)
    else addToQueue(track)
    setNotice({
      kind: 'info',
      text: `«${stripExt(track.name)}» — ${mode === 'next' ? 'следующим' : 'в конец очереди'}`,
    })
  }

  const handleCreatePlaylist = async () => {
    const name = newName.trim()
    if (!name || busy) return
    setBusy(true)
    setPanelError(null)
    let created = false
    try {
      await playlistsApi.create(name)
      created = true
      setNewName('')
      setPlaylists(await playlistsApi.list())
    } catch (e) {
      setPanelError(created ? 'Плейлист создан, но список не обновлён. Обновите список.' : 'Создание не подтверждено. Обновите список перед повтором.')
    } finally {
      setBusy(false)
    }
  }

  const handlePlayPlaylist = async (pl) => {
    if (playBusyId) return
    setPlayBusyId(pl.id)
    setPanelError(null)
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
      setPanelError(e?.message || 'Не удалось выполнить действие. Обновите список перед повтором.')
    } finally {
      setPlayBusyId(null)
    }
  }

  const handleDeletePlaylist = async (pl) => {
    setPanelError(null)
    try {
      await playlistsApi.remove(pl.id)
      setPlaylists((prev) => prev.filter((x) => x.id !== pl.id))
      if (openPlId === pl.id) {
        setOpenPlId(null)
        setPlTracks([])
      }
    } catch (e) {
      setPanelError(e?.message || 'Не удалось выполнить действие. Обновите список перед повтором.')
    }
  }

  const toggleOpenPlaylist = async (pl) => {
    if (openPlId === pl.id) {
      setOpenPlId(null)
      setPlTracks([])
      return
    }
    setOpenPlId(pl.id)
    setPlLoading(true)
    try {
      const detail = await playlistsApi.get(pl.id)
      setPlTracks(detail?.tracks || [])
    } catch {
      setPanelError('Не удалось загрузить треки плейлиста.')
    } finally {
      setPlLoading(false)
    }
  }

  const persistOrder = async (tracks) => {
    if (!openPlId || !tracks.length) return
    setReorderBusy(true)
    try {
      await playlistsApi.reorder(openPlId, tracks.map((t) => t.id))
      setPlTracks(tracks)
      setPlaylists(await playlistsApi.list())
    } catch (e) {
      setPanelError(e?.message || 'Не удалось выполнить действие. Обновите список перед повтором.')
      try {
        const detail = await playlistsApi.get(openPlId)
        setPlTracks(detail?.tracks || [])
      } catch {
        setPanelError('Порядок не подтверждён, обновление тоже не удалось. Повторите обновление списка.')
      }
    } finally {
      setReorderBusy(false)
    }
  }

  const moveTrack = (from, to) => {
    if (from === to || to < 0 || to >= plTracks.length || reorderBusy) return
    const next = [...plTracks]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    setPlTracks(next)
    persistOrder(next)
  }
  const onRowDragStart = (idx) => {
    dragActiveRef.current = true
    setDragIdx(idx)
  }
  const onRowDragOver = (e, idx) => {
    e.preventDefault()
    if (!dragActiveRef.current || dragIdx === null || dragIdx === idx) return
    const next = [...plTracks]
    const [item] = next.splice(dragIdx, 1)
    next.splice(idx, 0, item)
    setDragIdx(idx)
    setPlTracks(next)
    plTracksRef.current = next
  }
  const onRowDragEnd = () => {
    if (!dragActiveRef.current) return
    dragActiveRef.current = false
    setDragIdx(null)
    persistOrder(plTracksRef.current)
  }

  const rows = continueItems.length ? continueItems : recentItems

  return (
    <div className="tmusic-library-panel">
      {panelError && <div role="alert" style={{ color: 'var(--danger)', marginBottom: 12 }}>
        {panelError}
        <button type="button" onClick={() => setRefreshTick((value) => value + 1)}>Обновить список</button>
      </div>}
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
                  <ClickableRow
                    key={`${row.file_id}-${row.updated_at || row.started_at || idx}`}
                    className="tmusic-panel-row"
                    onClick={() => playRow(row)}
                  >
                    <Play size={13} fill="var(--accent)" />
                    <div className="tmusic-panel-row-main">
                      <div className="tmusic-panel-row-title" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span>{track.name}</span>
                        {cachedTrackIds.has(Number(row.file_id)) && (
                          <span
                            className="tmusic-badge-cached"
                            title="В кэше • Доступно офлайн"
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '2px',
                              fontSize: '10px',
                              fontWeight: '800',
                              padding: '1px 5px',
                              borderRadius: '5px',
                              background: 'rgba(34, 197, 94, 0.15)',
                              color: 'var(--success)',
                              border: '1px solid rgba(34, 197, 94, 0.3)',
                              flexShrink: 0,
                            }}
                          >
                            <Check size={10} strokeWidth={3} /> Кэш
                          </span>
                        )}
                      </div>
                      <div className="tmusic-panel-row-sub">
                        {typeof row.position_sec === 'number'
                          ? `${formatTime(row.position_sec)}${
                              row.duration_sec ? ' / ' + formatTime(row.duration_sec) : ''
                            }`
                          : 'недавно'}
                      </div>
                    </div>
                    <span className="tm-queue-actions" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className="tm-queue-mini"
                        onClick={() => queueRow(row, 'next')}
                        aria-label="Играть следующим"
                        title="Играть следующим"
                      >
                        <ListStart size={13} />
                      </button>
                      <button
                        type="button"
                        className="tm-queue-mini"
                        onClick={() => queueRow(row, 'queue')}
                        aria-label="Добавить в очередь"
                        title="Добавить в очередь"
                      >
                        <ListEnd size={13} />
                      </button>
                    </span>
                  </ClickableRow>
                )
              })}
            </div>
          )}
        </section>
      )}

      {/* Legacy: встроенная панель плейлистов. С 2026-10-12 «Плейлисты» из меню
          профиля открываются отдельным окном (components/PlaylistsModal.jsx),
          поэтому эта ветка больше не выбирается через onSelectSection. Оставлена
          как есть до отдельной уборки вместе со своими regression-тестами
          (tests/error-handling.test.cjs). */}
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
                <div key={pl.id} style={{ marginBottom: 8 }}>
                  <div className="tmusic-panel-row">
                    <button
                      className="tmusic-panel-row-main"
                      style={{ background: 'none', border: 'none', color: 'inherit', textAlign: 'left', cursor: 'pointer', flex: 1 }}
                      onClick={() => toggleOpenPlaylist(pl)}
                      title="Показать треки / порядок"
                    >
                      <div className="tmusic-panel-row-title">{pl.name}</div>
                      <div className="tmusic-panel-row-sub">
                        {pl.track_count || 0} трек(ов)
                        {openPlId === pl.id ? ' • свернуть' : ' • порядок ↑↓'}
                      </div>
                    </button>
                    <button
                      style={chipBtn}
                      onClick={() => handlePlayPlaylist(pl)}
                      disabled={playBusyId === pl.id}
                      aria-busy={playBusyId === pl.id || undefined}
                    >
                      <Play size={12} fill="currentColor" /> Слушать
                    </button>
                    <button
                      className="tmusic-panel-icon-btn"
                      onClick={() => handleDeletePlaylist(pl)}
                      title="Удалить"
                      aria-label={`Удалить плейлист ${pl.name}`}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>

                  {openPlId === pl.id && (
                    <div
                      style={{
                        marginTop: 6,
                        marginLeft: 12,
                        borderLeft: '2px solid var(--line)',
                        paddingLeft: 8,
                      }}
                    >
                      {plLoading ? (
                        <div className="tmusic-panel-empty">Загрузка треков…</div>
                      ) : plTracks.length === 0 ? (
                        <div className="tmusic-panel-empty">В плейлисте пусто</div>
                      ) : (
                        <div className="tmusic-panel-list">
                          {plTracks.map((t, idx) => (
                            <div
                              key={t.id}
                              className="tmusic-panel-row"
                              draggable={!reorderBusy}
                              onDragStart={() => onRowDragStart(idx)}
                              onDragOver={(e) => onRowDragOver(e, idx)}
                              onDragEnd={onRowDragEnd}
                              onDrop={(e) => e.preventDefault()}
                              style={{
                                opacity: reorderBusy ? 0.6 : 1,
                                cursor: 'grab',
                                backgroundColor: dragIdx === idx ? 'var(--accent-muted)' : undefined,
                              }}
                            >
                              <GripVertical size={13} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                              <ClickableRow
                                className="tmusic-panel-row-main"
                                onClick={() =>
                                  playQueue(
                                    plTracks.map((x) => ({
                                      id: x.file_id,
                                      name: x.file_name || `Трек ${x.file_id}`,
                                      stream_url: x.stream_url,
                                      folder_id: null,
                                      mime_type: x.mime_type || 'audio/mpeg',
                                      size: x.size ?? null,
                                    })),
                                    idx,
                                    { title: pl.name, artistName: 'Плейлист', coverUrl: null }
                                  )
                                }
                              >
                                <div className="tmusic-panel-row-title" style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span>{idx + 1}. {t.file_name || `Трек ${t.file_id}`}</span>
                                  {cachedTrackIds.has(Number(t.file_id)) && (
                                    <span
                                      className="tmusic-badge-cached"
                                      title="В кэше • Доступно офлайн"
                                      style={{
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        gap: '2px',
                                        fontSize: '10px',
                                        fontWeight: '800',
                                        padding: '1px 5px',
                                        borderRadius: '5px',
                                        background: 'rgba(34, 197, 94, 0.15)',
                                        color: 'var(--success)',
                                        border: '1px solid rgba(34, 197, 94, 0.3)',
                                        flexShrink: 0,
                                      }}
                                    >
                                      <Check size={10} strokeWidth={3} /> Кэш
                                    </span>
                                  )}
                                </div>
                              </ClickableRow>
                              <button
                                type="button"
                                style={iconBtn}
                                title="Выше"
                                disabled={idx === 0 || reorderBusy}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  moveTrack(idx, idx - 1)
                                }}
                              >
                                <ArrowUp size={13} />
                              </button>
                              <button
                                type="button"
                                style={iconBtn}
                                title="Ниже"
                                disabled={idx === plTracks.length - 1 || reorderBusy}
                                onClick={(e) => {
                                  e.stopPropagation()
                                  moveTrack(idx, idx + 1)
                                }}
                              >
                                <ArrowDown size={13} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
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
