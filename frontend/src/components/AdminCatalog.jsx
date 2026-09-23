/**
 * T-Music Admin Catalog — curation for admins only.
 * Rename artists/albums/collections, change kinds, create/delete collections,
 * move albums between collections (select or drag-and-drop), set covers.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  Pencil,
  Check,
  X,
  Trash2,
  Plus,
  Image as ImageIcon,
  ChevronDown,
  ChevronRight,
  GripVertical,
  Loader2,
  AlertTriangle,
  FolderHeart,
} from 'lucide-react'
import { adminApi } from '../api/admin'
import { bustCatalogCache } from '../api/library'

const KINDS = [
  { id: 'artist', label: 'Исполнитель' },
  { id: 'compilation', label: 'Сборник' },
  { id: 'standalone_album', label: 'Отдельный релиз' },
  { id: 'genre_collection', label: 'Коллекция' },
]

const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.id, k.label]))

const inputStyle = {
  background: 'var(--bg)',
  border: '1px solid var(--line)',
  color: 'var(--ink)',
  borderRadius: 8,
  padding: '8px 10px',
  minHeight: 40,
  fontSize: 14,
}

const iconBtn = {
  background: 'transparent',
  border: '1px solid var(--line)',
  color: 'var(--dim)',
  borderRadius: 8,
  minWidth: 36,
  minHeight: 36,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
}

function Row({ children, dropHl, ...rest }) {
  return (
    <div
      {...rest}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '10px 8px',
        borderBottom: '1px solid var(--line)',
        borderRadius: 8,
        background: dropHl ? 'var(--accent-muted)' : 'transparent',
        flexWrap: 'wrap',
        ...(rest.style || {}),
      }}
    >
      {children}
    </div>
  )
}

export function AdminCatalog({ onOpenCoverPicker }) {
  const [kind, setKind] = useState('')
  const [search, setSearch] = useState('')
  const [artists, setArtists] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null) // artist id
  const [albumsByArtist, setAlbumsByArtist] = useState({})
  const [editingArtist, setEditingArtist] = useState(null) // {id, name}
  const [editingAlbum, setEditingAlbum] = useState(null) // {id, title, year}
  const [busyId, setBusyId] = useState(null)
  const [dragAlbum, setDragAlbum] = useState(null)
  const [dropTarget, setDropTarget] = useState(null)
  const [newName, setNewName] = useState('')
  const [newKind, setNewKind] = useState('genre_collection')
  const [creating, setCreating] = useState(false)
  const loadSeq = useRef(0)

  const loadArtists = useCallback(async () => {
    const mySeq = ++loadSeq.current
    setLoading(true)
    setError(null)
    try {
      const data = await adminApi.catalogArtists({
        ...(kind ? { kind } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
        limit: 200,
      })
      if (mySeq === loadSeq.current) setArtists(data || [])
    } catch (e) {
      if (mySeq === loadSeq.current) {
        setError(e.message || 'Не удалось загрузить каталог')
        setArtists([])
      }
    } finally {
      if (mySeq === loadSeq.current) setLoading(false)
    }
  }, [kind, search])

  useEffect(() => {
    const t = setTimeout(loadArtists, 300)
    return () => clearTimeout(t)
  }, [loadArtists])

  const refresh = async () => {
    bustCatalogCache()
    setAlbumsByArtist({})
    await loadArtists()
  }

  const loadAlbums = async (artistId) => {
    try {
      const data = await adminApi.catalogAlbums({ artist_id: artistId, limit: 500 })
      setAlbumsByArtist((prev) => ({ ...prev, [artistId]: data || [] }))
    } catch (e) {
      setError(e.message || 'Не удалось загрузить альбомы')
    }
  }

  const toggleExpand = (artist) => {
    if (expanded === artist.id) {
      setExpanded(null)
    } else {
      setExpanded(artist.id)
      if (!albumsByArtist[artist.id]) loadAlbums(artist.id)
    }
  }

  const saveArtistName = async (artist) => {
    const name = (editingArtist?.name || '').trim()
    if (!name || name === artist.display_name) {
      setEditingArtist(null)
      return
    }
    setBusyId(`a${artist.id}`)
    try {
      await adminApi.patchArtist(artist.id, { display_name: name })
      setEditingArtist(null)
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось переименовать')
    } finally {
      setBusyId(null)
    }
  }

  const changeKind = async (artist, nextKind) => {
    if (nextKind === artist.kind) return
    setBusyId(`a${artist.id}`)
    try {
      await adminApi.patchArtist(artist.id, { kind: nextKind })
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось сменить тип')
    } finally {
      setBusyId(null)
    }
  }

  const removeArtist = async (artist) => {
    if (!confirm(`Удалить «${artist.display_name}»? Возможно только если внутри нет альбомов.`)) return
    setBusyId(`a${artist.id}`)
    try {
      await adminApi.deleteArtist(artist.id)
      if (expanded === artist.id) setExpanded(null)
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось удалить')
    } finally {
      setBusyId(null)
    }
  }

  const createCollection = async () => {
    const name = newName.trim()
    if (!name || creating) return
    setCreating(true)
    setError(null)
    try {
      await adminApi.createCollection({ display_name: name, kind: newKind })
      setNewName('')
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось создать коллекцию')
    } finally {
      setCreating(false)
    }
  }

  const saveAlbum = async (album, artistId) => {
    const title = (editingAlbum?.title || '').trim()
    const yearRaw = (editingAlbum?.year || '').toString().trim()
    const year = yearRaw === '' ? null : Number(yearRaw)
    if (year !== null && (!Number.isInteger(year) || year < 1800 || year > 2100)) {
      setError('Год должен быть от 1800 до 2100 (или пусто)')
      return
    }
    if (!title) {
      setError('Название альбома не может быть пустым')
      return
    }
    setBusyId(`b${album.id}`)
    try {
      await adminApi.patchAlbum(album.id, {
        title,
        ...(year === null ? {} : { year }),
        ...(yearRaw === '' && album.year != null ? { year: 0 } : {}),
      })
      setEditingAlbum(null)
      bustCatalogCache()
      await loadAlbums(artistId)
    } catch (e) {
      setError(e.message || 'Не удалось сохранить альбом')
    } finally {
      setBusyId(null)
    }
  }

  const moveAlbum = async (album, targetArtistId) => {
    if (targetArtistId === album.artist_id) return
    setBusyId(`b${album.id}`)
    try {
      await adminApi.patchAlbum(album.id, { artist_id: targetArtistId })
      setDragAlbum(null)
      setDropTarget(null)
      bustCatalogCache()
      // Album left its old parent — reload both sides.
      setAlbumsByArtist({})
      await loadArtists()
      if (expanded) loadAlbums(expanded === album.artist_id ? targetArtistId : expanded)
    } catch (e) {
      setError(e.message || 'Не удалось переместить альбом')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      {error && (
        <div
          style={{
            marginBottom: 12,
            padding: '10px 12px',
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            background: 'rgba(255,107,107,0.12)',
            color: 'var(--danger)',
            border: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <AlertTriangle size={14} /> {error}
          <button type="button" onClick={() => setError(null)} style={{ ...iconBtn, marginLeft: 'auto' }} aria-label="Закрыть">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Create collection */}
      <div
        style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--line)',
          borderRadius: 12,
          padding: '14px 16px',
          marginBottom: 14,
        }}
      >
        <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <FolderHeart size={15} style={{ color: 'var(--accent)' }} /> Новая коллекция
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Название коллекции"
            onKeyDown={(e) => {
              if (e.key === 'Enter') createCollection()
            }}
            style={{ ...inputStyle, flex: 1, minWidth: 160 }}
          />
          <select value={newKind} onChange={(e) => setNewKind(e.target.value)} style={{ ...inputStyle, minHeight: 40 }}>
            {KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={creating || !newName.trim()}
            onClick={createCollection}
            style={{
              background: 'var(--accent)',
              color: '#141210',
              border: 'none',
              borderRadius: 8,
              padding: '8px 14px',
              minHeight: 40,
              fontWeight: 800,
              fontSize: 12,
              cursor: creating || !newName.trim() ? 'default' : 'pointer',
              opacity: creating || !newName.trim() ? 0.5 : 1,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Создать
          </button>
        </div>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск по каталогу…"
          style={{ ...inputStyle, flex: 1, minWidth: 160 }}
        />
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ ...inputStyle }}>
          <option value="">Все типы</option>
          {KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
          <Loader2 size={20} className="animate-spin" /> Загрузка…
        </div>
      )}

      {!loading && artists.length === 0 && (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>Ничего не найдено</div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {artists.map((artist) => {
          const isOpen = expanded === artist.id
          const albums = albumsByArtist[artist.id] || []
          const isEditing = editingArtist?.id === artist.id
          const busy = busyId === `a${artist.id}`
          return (
            <div
              key={artist.id}
              onDragOver={(e) => {
                if (dragAlbum && dragAlbum.artist_id !== artist.id) {
                  e.preventDefault()
                  setDropTarget(artist.id)
                }
              }}
              onDragLeave={() => setDropTarget((t) => (t === artist.id ? null : t))}
              onDrop={(e) => {
                e.preventDefault()
                if (dragAlbum && dragAlbum.artist_id !== artist.id) moveAlbum(dragAlbum, artist.id)
              }}
              style={{
                border: '1px solid var(--line)',
                borderRadius: 10,
                marginBottom: 8,
                background: dropTarget === artist.id ? 'var(--accent-muted)' : 'var(--bg-card)',
              }}
            >
              <Row dropHl={false}>
                <button
                  type="button"
                  onClick={() => toggleExpand(artist)}
                  style={{ ...iconBtn, border: 'none' }}
                  aria-label={isOpen ? 'Свернуть' : 'Развернуть'}
                >
                  {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                {isEditing ? (
                  <>
                    <input
                      value={editingArtist.name}
                      onChange={(e) => setEditingArtist({ id: artist.id, name: e.target.value })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveArtistName(artist)
                        if (e.key === 'Escape') setEditingArtist(null)
                      }}
                      autoFocus
                      style={{ ...inputStyle, flex: 1, minWidth: 120 }}
                    />
                    <button type="button" onClick={() => saveArtistName(artist)} disabled={busy} style={iconBtn} aria-label="Сохранить">
                      <Check size={15} style={{ color: 'var(--accent)' }} />
                    </button>
                    <button type="button" onClick={() => setEditingArtist(null)} style={iconBtn} aria-label="Отмена">
                      <X size={15} />
                    </button>
                  </>
                ) : (
                  <>
                    <div style={{ flex: 1, minWidth: 120 }}>
                      <div style={{ fontWeight: 700, fontSize: 13.5 }}>{artist.display_name}</div>
                      <div style={{ fontSize: 11, color: 'var(--dim)' }}>
                        {KIND_LABEL[artist.kind] || artist.kind} · {artist.album_count ?? 0} альб.
                        {artist.locked ? ' · закреплён' : ''}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setEditingArtist({ id: artist.id, name: artist.display_name })}
                      style={iconBtn}
                      title="Переименовать"
                      aria-label={`Переименовать ${artist.display_name}`}
                    >
                      <Pencil size={14} />
                    </button>
                  </>
                )}
                <select
                  value={artist.kind}
                  disabled={busy}
                  onChange={(e) => changeKind(artist, e.target.value)}
                  title="Тип записи"
                  style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 150 }}
                >
                  {KINDS.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.label}
                    </option>
                  ))}
                </select>
                {onOpenCoverPicker && (
                  <button
                    type="button"
                    onClick={() =>
                      onOpenCoverPicker({
                        type: 'artist',
                        id: artist.id,
                        displayName: artist.display_name,
                        hasCustom: !!artist.custom_image_url,
                      })
                    }
                    style={iconBtn}
                    title="Обложка: файл или URL"
                    aria-label={`Обложка ${artist.display_name}`}
                  >
                    <ImageIcon size={14} />
                  </button>
                )}
                <button type="button" onClick={() => removeArtist(artist)} disabled={busy} style={iconBtn} title="Удалить (только пустую)" aria-label={`Удалить ${artist.display_name}`}>
                  <Trash2 size={14} />
                </button>
              </Row>

              {isOpen && (
                <div style={{ padding: '4px 8px 10px 44px' }}>
                  {albums.length === 0 && (
                    <div style={{ fontSize: 12, color: 'var(--dim)', padding: '6px 0' }}>
                      Альбомов нет — перетащите сюда альбом из другой коллекции
                    </div>
                  )}
                  {albums.map((album) => {
                    const albEditing = editingAlbum?.id === album.id
                    const albBusy = busyId === `b${album.id}`
                    return (
                      <div
                        key={album.id}
                        draggable
                        onDragStart={() => setDragAlbum(album)}
                        onDragEnd={() => {
                          setDragAlbum(null)
                          setDropTarget(null)
                        }}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '8px 6px',
                          borderBottom: '1px solid var(--line)',
                          flexWrap: 'wrap',
                          opacity: dragAlbum?.id === album.id ? 0.5 : 1,
                        }}
                      >
                        <span title="Перетащите в другую коллекцию" style={{ color: 'var(--dim)', cursor: 'grab', display: 'inline-flex' }}>
                          <GripVertical size={15} />
                        </span>
                        {albEditing ? (
                          <>
                            <input
                              value={editingAlbum.title}
                              onChange={(e) => setEditingAlbum({ ...editingAlbum, title: e.target.value })}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') saveAlbum(album, artist.id)
                                if (e.key === 'Escape') setEditingAlbum(null)
                              }}
                              autoFocus
                              placeholder="Название альбома"
                              style={{ ...inputStyle, flex: 2, minWidth: 140 }}
                            />
                            <input
                              value={editingAlbum.year}
                              onChange={(e) => setEditingAlbum({ ...editingAlbum, year: e.target.value })}
                              placeholder="Год"
                              inputMode="numeric"
                              style={{ ...inputStyle, width: 84 }}
                            />
                            <button type="button" onClick={() => saveAlbum(album, artist.id)} disabled={albBusy} style={iconBtn} aria-label="Сохранить альбом">
                              <Check size={15} style={{ color: 'var(--accent)' }} />
                            </button>
                            <button type="button" onClick={() => setEditingAlbum(null)} style={iconBtn} aria-label="Отмена">
                              <X size={15} />
                            </button>
                          </>
                        ) : (
                          <>
                            <div style={{ flex: 1, minWidth: 140, fontSize: 13 }}>
                              <b>{album.title}</b>
                              <span style={{ color: 'var(--dim)' }}> · {album.year || 'без года'}</span>
                            </div>
                            <button
                              type="button"
                              onClick={() =>
                                setEditingAlbum({ id: album.id, title: album.title, year: album.year || '' })
                              }
                              style={iconBtn}
                              title="Переименовать / год"
                              aria-label={`Редактировать ${album.title}`}
                            >
                              <Pencil size={13} />
                            </button>
                          </>
                        )}
                        <select
                          value={album.artist_id}
                          disabled={albBusy}
                          onChange={(e) => moveAlbum(album, Number(e.target.value))}
                          title="Переместить в коллекцию"
                          style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 170 }}
                        >
                          {artists.map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.display_name.slice(0, 28)}
                            </option>
                          ))}
                        </select>
                        {onOpenCoverPicker && (
                          <button
                            type="button"
                            onClick={() =>
                              onOpenCoverPicker({
                                type: 'album',
                                id: album.id,
                                title: album.title,
                                hasCustom: !!album.custom_image_url,
                              })
                            }
                            style={iconBtn}
                            title="Обложка: файл или URL"
                            aria-label={`Обложка ${album.title}`}
                          >
                            <ImageIcon size={13} />
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 8 }}>
        Переименование закрепляет запись — автообогащение её больше не перезапишет. Перемещение: выберите коллекцию
        в списке или перетащите альбом на другую коллекцию.
      </div>
    </div>
  )
}
