/**
 * T-Music PlaylistsModal — отдельное окно «Плейлисты».
 *
 * Открывается из пункта «Плейлисты» в выпадающем меню профиля (см. ProfileMenu /
 * MusicLibrary). Даёт полноценное редактирование: создание, переименование,
 * удаление, а также содержимое плейлиста — добавление треков через поиск,
 * удаление и перестановка (drag или кнопки ↑↓).
 *
 * Список и карточка плейлиста живут в одном окне: на широком экране — две
 * колонки, на узком — поочерёдно (список → содержимое с кнопкой «Назад»).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronLeft,
  GripVertical,
  HardDrive,
  ListMusic,
  ListPlus,
  Loader2,
  Music2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import Modal from './ui/Modal'
import { useConfirm } from './ui/useConfirm'
import { playlistsApi } from '../api/playlists'
import { searchApi } from '../api/search'
import { usePlayerStore } from '../store/playerStore'
import { useOfflineStore } from '../store/offlineStore'

const NARROW_QUERY = '(max-width: 760px)'

/** Media-query hook (без внешних зависимостей). */
function useIsNarrow(query = NARROW_QUERY) {
  const [narrow, setNarrow] = useState(() =>
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia(query).matches
      : false
  )
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return undefined
    const mq = window.matchMedia(query)
    const onChange = (e) => setNarrow(e.matches)
    setNarrow(mq.matches)
    if (mq.addEventListener) mq.addEventListener('change', onChange)
    else mq.addListener(onChange)
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange)
      else mq.removeListener(onChange)
    }
  }, [query])
  return narrow
}

const toQueueTrack = (t) => ({
  id: t.file_id,
  name: t.file_name || `Трек ${t.file_id}`,
  stream_url: t.stream_url,
  folder_id: null,
  mime_type: t.mime_type || 'audio/mpeg',
  size: t.size ?? null,
})

const ghostBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
  minHeight: 40,
  minWidth: 40,
  padding: '6px 12px',
  borderRadius: 9,
  border: '1px solid var(--line)',
  background: 'transparent',
  color: 'var(--ink)',
  fontSize: 12.5,
  fontWeight: 700,
  cursor: 'pointer',
  flexShrink: 0,
}

const accentBtn = {
  ...ghostBtn,
  border: 'none',
  background: 'var(--accent)',
  color: 'var(--on-accent)',
}

const iconBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 34,
  height: 34,
  borderRadius: 8,
  border: '1px solid var(--line)',
  background: 'transparent',
  color: 'var(--dim)',
  cursor: 'pointer',
  flexShrink: 0,
  padding: 0,
}

export function PlaylistsModal({ open, onClose }) {
  const narrow = useIsNarrow()
  const { confirm, confirmDialog } = useConfirm()
  const playQueue = usePlayerStore((s) => s.playQueue)
  const cachedTrackIds = useOfflineStore((s) => s.cachedTrackIds)

  const [playlists, setPlaylists] = useState([])
  const [listLoading, setListLoading] = useState(false)
  const [error, setError] = useState(null)

  const [newName, setNewName] = useState('')
  const [createBusy, setCreateBusy] = useState(false)

  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null) // { id, name, track_count, tracks }
  const [detailLoading, setDetailLoading] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [renameBusy, setRenameBusy] = useState(false)
  const [rowBusy, setRowBusy] = useState(false)
  const [playBusy, setPlayBusy] = useState(false)

  const [dragIdx, setDragIdx] = useState(null)
  const dragActiveRef = useRef(false)
  const tracksRef = useRef([])

  const [addQuery, setAddQuery] = useState('')
  const [addResults, setAddResults] = useState([])
  const [addLoading, setAddLoading] = useState(false)
  const [addingId, setAddingId] = useState(null)

  const tracks = detail?.tracks || []

  useEffect(() => {
    tracksRef.current = tracks
  }, [tracks])

  const loadList = useCallback(async ({ clearSelection = false } = {}) => {
    setListLoading(true)
    setError(null)
    try {
      const rows = await playlistsApi.list()
      setPlaylists(Array.isArray(rows) ? rows : [])
      if (clearSelection) {
        setSelectedId(null)
        setDetail(null)
        setNameDraft('')
      }
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить плейлисты. Обновите список.')
    } finally {
      setListLoading(false)
    }
  }, [])

  // Свежий старт при каждом открытии окна.
  useEffect(() => {
    if (!open) return
    setError(null)
    setNewName('')
    setAddQuery('')
    setAddResults([])
    setAddingId(null)
    setDragIdx(null)
    loadList({ clearSelection: true })
  }, [open, loadList])

  const openPlaylist = useCallback(async (pl) => {
    setSelectedId(pl.id)
    setNameDraft(pl.name || '')
    setDetail(null)
    setDetailLoading(true)
    setError(null)
    setAddQuery('')
    setAddResults([])
    try {
      const d = await playlistsApi.get(pl.id)
      setDetail(d)
      setNameDraft(d?.name ?? pl.name ?? '')
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить треки плейлиста.')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleCreate = async () => {
    const name = newName.trim()
    if (!name || createBusy) return
    setCreateBusy(true)
    setError(null)
    try {
      const created = await playlistsApi.create(name)
      setNewName('')
      await loadList()
      if (created?.id) await openPlaylist(created)
    } catch (e) {
      setError(e?.message || 'Не удалось создать плейлист.')
    } finally {
      setCreateBusy(false)
    }
  }

  const handleRename = async () => {
    const name = nameDraft.trim()
    if (!detail || renameBusy) return
    if (!name || name === detail.name) return
    setRenameBusy(true)
    setError(null)
    try {
      const updated = await playlistsApi.rename(detail.id, name)
      const applied = updated?.name ?? name
      setDetail((d) => (d ? { ...d, name: applied } : d))
      setNameDraft(applied)
      setPlaylists((prev) => prev.map((p) => (p.id === detail.id ? { ...p, name: applied } : p)))
      usePlayerStore.getState().pushNotice({ kind: 'info', text: 'Название плейлиста обновлено' })
    } catch (e) {
      setError(e?.message || 'Не удалось переименовать плейлист.')
    } finally {
      setRenameBusy(false)
    }
  }

  const handleDelete = async (pl) => {
    const ok = await confirm({
      title: 'Удалить плейлист',
      message: `Удалить «${pl.name}»? Действие необратимо.`,
      confirmLabel: 'Удалить',
      danger: true,
    })
    if (!ok) return
    setError(null)
    try {
      await playlistsApi.remove(pl.id)
      setPlaylists((prev) => prev.filter((p) => p.id !== pl.id))
      if (selectedId === pl.id) {
        setSelectedId(null)
        setDetail(null)
        setNameDraft('')
      }
    } catch (e) {
      setError(e?.message || 'Не удалось удалить плейлист.')
    }
  }

  const handlePlayAll = async () => {
    if (!detail || playBusy) return
    if (tracks.length) {
      playQueue(tracks.map(toQueueTrack), 0, {
        title: detail.name,
        artistName: 'Плейлист',
        coverUrl: null,
      })
      return
    }
    // Пустой плейлист: пробуем перечитать детали — возможно, список устарел.
    setPlayBusy(true)
    try {
      const d = await playlistsApi.get(detail.id)
      setDetail(d)
      const fresh = d?.tracks || []
      if (fresh.length) {
        playQueue(fresh.map(toQueueTrack), 0, {
          title: d.name,
          artistName: 'Плейлист',
          coverUrl: null,
        })
      }
    } catch (e) {
      setError(e?.message || 'Не удалось запустить плейлист.')
    } finally {
      setPlayBusy(false)
    }
  }

  const handlePlayTrack = (idx) => {
    if (!detail || !tracks.length) return
    playQueue(tracks.map(toQueueTrack), idx, {
      title: detail.name,
      artistName: 'Плейлист',
      coverUrl: null,
    })
  }

  const persistOrder = async (nextTracks) => {
    if (!detail || !nextTracks.length) return
    setRowBusy(true)
    try {
      await playlistsApi.reorder(detail.id, nextTracks.map((t) => t.id))
      setDetail((d) => (d ? { ...d, tracks: nextTracks } : d))
    } catch (e) {
      setError(e?.message || 'Не удалось сохранить порядок треков.')
      // Порядок не подтверждён — возвращаем серверную версию.
      try {
        const d = await playlistsApi.get(detail.id)
        setDetail(d)
      } catch {
        setError('Порядок не подтверждён, обновление тоже не удалось. Нажмите «Обновить».')
      }
    } finally {
      setRowBusy(false)
    }
  }

  const moveTrack = (from, to) => {
    if (rowBusy || from === to || to < 0 || to >= tracks.length) return
    const next = [...tracks]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    setDetail((d) => (d ? { ...d, tracks: next } : d))
    persistOrder(next)
  }

  const onRowDragStart = (idx) => {
    dragActiveRef.current = true
    setDragIdx(idx)
  }
  const onRowDragOver = (e, idx) => {
    e.preventDefault()
    if (!dragActiveRef.current || dragIdx === null || dragIdx === idx) return
    const next = [...tracksRef.current]
    const [item] = next.splice(dragIdx, 1)
    next.splice(idx, 0, item)
    setDragIdx(idx)
    tracksRef.current = next
    setDetail((d) => (d ? { ...d, tracks: next } : d))
  }
  const onRowDragEnd = () => {
    if (!dragActiveRef.current) return
    dragActiveRef.current = false
    setDragIdx(null)
    persistOrder(tracksRef.current)
  }

  const handleRemoveTrack = async (item) => {
    if (!detail || rowBusy) return
    setRowBusy(true)
    setError(null)
    try {
      await playlistsApi.removeItem(detail.id, item.id)
      const next = tracks.filter((t) => t.id !== item.id)
      setDetail((d) => (d ? { ...d, tracks: next, track_count: next.length } : d))
      setPlaylists((prev) =>
        prev.map((p) =>
          p.id === detail.id ? { ...p, track_count: Math.max(0, (p.track_count || 0) - 1) } : p
        )
      )
    } catch (e) {
      setError(e?.message || 'Не удалось убрать трек из плейлиста.')
    } finally {
      setRowBusy(false)
    }
  }

  // Поиск треков для добавления (с дебаунсом и отменой устаревшего ответа).
  useEffect(() => {
    if (!open) return undefined
    const q = addQuery.trim()
    if (!q) {
      setAddResults([])
      setAddLoading(false)
      return undefined
    }
    let cancelled = false
    setAddLoading(true)
    const timer = setTimeout(async () => {
      try {
        const res = await searchApi.tracks(q, { limit: 20 })
        if (!cancelled) setAddResults(res?.items || [])
      } catch {
        if (!cancelled) setError('Не удалось выполнить поиск треков. Повторите запрос.')
      } finally {
        if (!cancelled) setAddLoading(false)
      }
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [addQuery, open])

  const handleAddTrack = async (track) => {
    if (!detail || addingId) return
    setAddingId(track.id)
    setError(null)
    try {
      const before = tracks.length
      const updated = await playlistsApi.addTrack(detail.id, track.id)
      setDetail(updated)
      setPlaylists((prev) =>
        prev.map((p) =>
          p.id === detail.id
            ? { ...p, track_count: updated?.track_count ?? p.track_count }
            : p
        )
      )
      const after = updated?.tracks?.length ?? before
      usePlayerStore.getState().pushNotice({
        kind: 'info',
        text:
          after > before
            ? `«${track.title || track.file_name}» добавлен в плейлист`
            : `«${track.title || track.file_name}» уже в плейлисте`,
      })
    } catch (e) {
      setError(e?.message || 'Не удалось добавить трек в плейлист.')
    } finally {
      setAddingId(null)
    }
  }

  const renameDirty = Boolean(detail) && nameDraft.trim() !== '' && nameDraft.trim() !== detail.name
  const showSide = !narrow || !selectedId
  const showDetail = !narrow || Boolean(selectedId)

  const selectedSummary = useMemo(
    () => playlists.find((p) => p.id === selectedId) || null,
    [playlists, selectedId]
  )

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Плейлисты"
      maxWidth={960}
      maxHeight="92dvh"
      style={{ height: 'min(760px, 92dvh)', background: 'var(--bg-card)' }}
    >
      {confirmDialog}

      <div className="tmusic-pl">
        {/* ── Заголовок окна ───────────────────────────────────────────── */}
        <div className="tmusic-pl-head">
          <ListMusic size={18} style={{ color: 'var(--accent)' }} />
          <div className="tmusic-pl-head-title">
            Плейлисты
            <span className="tmusic-pl-head-sub">{playlists.length}</span>
          </div>
          <button
            type="button"
            onClick={() => loadList()}
            style={iconBtn}
            title="Обновить список"
            aria-label="Обновить список плейлистов"
            disabled={listLoading}
          >
            <RefreshCw size={15} className={listLoading ? 'tmusic-spin' : undefined} />
          </button>
          <button
            type="button"
            onClick={onClose}
            style={iconBtn}
            title="Закрыть"
            aria-label="Закрыть окно плейлистов"
          >
            <X size={16} />
          </button>
        </div>

        {error && (
          <div className="tmusic-pl-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => loadList()} className="tmusic-pl-error-btn">
              Обновить
            </button>
          </div>
        )}

        <div className="tmusic-pl-body">
          {/* ── Левая колонка: список ──────────────────────────────────── */}
          {showSide && (
            <div className="tmusic-pl-side">
              <div className="tmusic-pl-create">
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Новый плейлист"
                  aria-label="Название нового плейлиста"
                  maxLength={120}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleCreate()
                  }}
                />
                <button
                  type="button"
                  onClick={handleCreate}
                  disabled={createBusy || !newName.trim()}
                  style={{ ...accentBtn, opacity: createBusy || !newName.trim() ? 0.5 : 1 }}
                  title="Создать плейлист"
                >
                  {createBusy ? <Loader2 size={14} className="tmusic-spin" /> : <Plus size={14} />}
                  Создать
                </button>
              </div>

              {listLoading && playlists.length === 0 ? (
                <div className="tmusic-pl-empty">
                  <Loader2 size={16} className="tmusic-spin" /> Загрузка…
                </div>
              ) : playlists.length === 0 ? (
                <div className="tmusic-pl-empty">Плейлистов нет — создайте первый</div>
              ) : (
                <div className="tmusic-pl-list" role="list">
                  {playlists.map((pl) => {
                    const active = pl.id === selectedId
                    return (
                      <div
                        key={pl.id}
                        className={`tmusic-pl-item ${active ? 'active' : ''}`}
                        role="listitem"
                      >
                        <button
                          type="button"
                          className="tmusic-pl-item-main"
                          onClick={() => openPlaylist(pl)}
                          title="Открыть плейлист"
                          aria-current={active ? 'true' : undefined}
                        >
                          <span className="tmusic-pl-item-name">{pl.name}</span>
                          <span className="tmusic-pl-item-sub">
                            {pl.track_count || 0} трек(ов)
                          </span>
                        </button>
                        <button
                          type="button"
                          className="tmusic-pl-icon-btn"
                          onClick={() => handleDelete(pl)}
                          title="Удалить плейлист"
                          aria-label={`Удалить плейлист ${pl.name}`}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── Правая колонка: содержимое ─────────────────────────────── */}
          {showDetail && (
            <div className="tmusic-pl-detail">
              {!selectedId ? (
                <div className="tmusic-pl-empty tmusic-pl-empty-center">
                  <ListMusic size={30} style={{ opacity: 0.5 }} />
                  <div>Выберите плейлист слева, чтобы отредактировать название и треки</div>
                </div>
              ) : (
                <>
                  {narrow && (
                    <button
                      type="button"
                      className="tmusic-pl-back"
                      onClick={() => {
                        setSelectedId(null)
                        setDetail(null)
                        setNameDraft('')
                      }}
                    >
                      <ChevronLeft size={15} /> К списку плейлистов
                    </button>
                  )}

                  <div className="tmusic-pl-rename">
                    <Pencil size={14} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                    <input
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      aria-label="Название плейлиста"
                      maxLength={120}
                      placeholder="Название плейлиста"
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRename()
                      }}
                    />
                    <button
                      type="button"
                      onClick={handleRename}
                      disabled={!renameDirty || renameBusy}
                      style={{
                        ...accentBtn,
                        opacity: !renameDirty || renameBusy ? 0.45 : 1,
                        cursor: !renameDirty || renameBusy ? 'default' : 'pointer',
                      }}
                      title={renameDirty ? 'Сохранить название' : 'Название не изменено'}
                      aria-label="Сохранить название плейлиста"
                    >
                      {renameBusy ? <Loader2 size={14} className="tmusic-spin" /> : <Check size={14} />}
                      Сохранить
                    </button>
                  </div>

                  <div className="tmusic-pl-actions">
                    <button
                      type="button"
                      onClick={handlePlayAll}
                      disabled={playBusy}
                      style={accentBtn}
                      title="Слушать плейлист"
                    >
                      {playBusy ? <Loader2 size={14} className="tmusic-spin" /> : <Play size={13} fill="currentColor" />}
                      Слушать
                    </button>
                    <span className="tmusic-pl-count">
                      {tracks.length} трек(ов)
                      {rowBusy ? ' • сохранение…' : ''}
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        handleDelete(
                          selectedSummary || {
                            id: selectedId,
                            name: detail?.name || nameDraft || 'плейлист',
                          }
                        )
                      }
                      style={{ ...ghostBtn, marginLeft: 'auto', color: 'var(--danger)' }}
                      title="Удалить плейлист"
                    >
                      <Trash2 size={14} /> Удалить
                    </button>
                  </div>

                  {/* Добавление треков через поиск по медиатеке */}
                  <div className="tmusic-pl-add">
                    <div className="tmusic-pl-search">
                      <Search size={15} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                      <input
                        value={addQuery}
                        onChange={(e) => setAddQuery(e.target.value)}
                        placeholder="Найти трек и добавить в плейлист…"
                        aria-label="Поиск треков для добавления"
                      />
                      {addLoading && <Loader2 size={15} className="tmusic-spin" style={{ color: 'var(--accent)' }} />}
                      {addQuery && !addLoading && (
                        <button
                          type="button"
                          onClick={() => setAddQuery('')}
                          style={{ ...iconBtn, width: 28, height: 28 }}
                          aria-label="Очистить поиск"
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                    {addQuery.trim() && (
                      <div className="tmusic-pl-results">
                        {!addLoading && addResults.length === 0 ? (
                          <div className="tmusic-pl-empty">Ничего не найдено</div>
                        ) : (
                          addResults.map((t) => (
                            <div key={t.id} className="tmusic-pl-result">
                              <Music2 size={14} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                              <div className="tmusic-pl-result-main">
                                <div className="tmusic-pl-result-title">{t.title || t.file_name}</div>
                                <div className="tmusic-pl-result-sub">
                                  {[t.artist_name, t.album_title].filter(Boolean).join(' • ') || 'трек'}
                                </div>
                              </div>
                              <button
                                type="button"
                                className="tmusic-pl-icon-btn"
                                onClick={() => handleAddTrack(t)}
                                disabled={addingId === t.id}
                                title="Добавить в плейлист"
                                aria-label={`Добавить «${t.title || t.file_name}» в плейлист`}
                              >
                                {addingId === t.id ? (
                                  <Loader2 size={14} className="tmusic-spin" />
                                ) : (
                                  <ListPlus size={14} />
                                )}
                              </button>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>

                  {/* Список треков плейлиста */}
                  <div className="tmusic-pl-tracks">
                    {detailLoading ? (
                      <div className="tmusic-pl-empty">
                        <Loader2 size={16} className="tmusic-spin" /> Загрузка треков…
                      </div>
                    ) : tracks.length === 0 ? (
                      <div className="tmusic-pl-empty">
                        В плейлисте пусто — найдите трек выше и добавьте его
                      </div>
                    ) : (
                      tracks.map((t, idx) => (
                        <div
                          key={t.id}
                          className="tmusic-pl-track"
                          draggable={!rowBusy}
                          onDragStart={() => onRowDragStart(idx)}
                          onDragOver={(e) => onRowDragOver(e, idx)}
                          onDragEnd={onRowDragEnd}
                          onDrop={(e) => e.preventDefault()}
                          style={{
                            opacity: rowBusy ? 0.65 : 1,
                            background: dragIdx === idx ? 'var(--accent-muted)' : undefined,
                          }}
                        >
                          <GripVertical size={14} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                          <button
                            type="button"
                            className="tmusic-pl-track-main"
                            onClick={() => handlePlayTrack(idx)}
                            title="Слушать с этого трека"
                          >
                            <span className="tmusic-pl-track-title">
                              <span className="tmusic-pl-track-num">{idx + 1}.</span>
                              <span className="tmusic-pl-track-name">
                                {t.file_name || `Трек ${t.file_id}`}
                              </span>
                              {cachedTrackIds.has(Number(t.file_id)) && (
                                <span className="tmusic-pl-cached" title="В кэше • доступно офлайн">
                                  <HardDrive size={11} /> Кэш
                                </span>
                              )}
                            </span>
                          </button>
                          <button
                            type="button"
                            style={iconBtn}
                            disabled={idx === 0 || rowBusy}
                            onClick={() => moveTrack(idx, idx - 1)}
                            title="Выше"
                            aria-label={`Поднять трек ${t.file_name || t.file_id}`}
                          >
                            <ArrowUp size={14} />
                          </button>
                          <button
                            type="button"
                            style={iconBtn}
                            disabled={idx === tracks.length - 1 || rowBusy}
                            onClick={() => moveTrack(idx, idx + 1)}
                            title="Ниже"
                            aria-label={`Опустить трек ${t.file_name || t.file_id}`}
                          >
                            <ArrowDown size={14} />
                          </button>
                          <button
                            type="button"
                            style={iconBtn}
                            disabled={rowBusy}
                            onClick={() => handleRemoveTrack(t)}
                            title="Убрать из плейлиста"
                            aria-label={`Убрать «${t.file_name || t.file_id}» из плейлиста`}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}

export default PlaylistsModal
