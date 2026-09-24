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
  MoreVertical,
  ScanLine,
  FolderInput,
} from 'lucide-react'
import { adminApi } from '../api/admin'
import { bustCatalogCache } from '../api/library'

const KINDS = [
  { id: 'artist', label: 'Артист' },
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

function ActionMenu({ children }) {
  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        display: 'flex',
        gap: 6,
        flexWrap: 'wrap',
        padding: '8px',
        margin: '0 8px 8px 44px',
        border: '1px solid var(--line)',
        borderRadius: 8,
        background: 'var(--bg)',
      }}
    >
      {children}
    </div>
  )
}

function MenuAction({ icon: Icon, children, onClick, disabled = false, danger = false }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{
        ...iconBtn,
        width: 'auto',
        padding: '0 10px',
        gap: 6,
        minHeight: 34,
        color: danger ? 'var(--danger)' : 'var(--ink)',
        fontSize: 12,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <Icon size={13} /> {children}
    </button>
  )
}

export function AdminCatalog({ onOpenCoverPicker }) {
  const [kind, setKind] = useState('')
  const [search, setSearch] = useState('')
  const [artists, setArtists] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expanded, setExpanded] = useState(null)
  const [albumsByArtist, setAlbumsByArtist] = useState({})
  const [editingArtist, setEditingArtist] = useState(null)
  const [editingAlbum, setEditingAlbum] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [dragAlbum, setDragAlbum] = useState(null)
  const [dropTarget, setDropTarget] = useState(null)
  const [newName, setNewName] = useState('')
  const [newKind, setNewKind] = useState('genre_collection')
  const [creating, setCreating] = useState(false)
  const [selectedArtists, setSelectedArtists] = useState([])
  const [selectedAlbums, setSelectedAlbums] = useState([])
  const [moveTarget, setMoveTarget] = useState('')
  const [menu, setMenu] = useState(null)
  const loadSeq = useRef(0)

  const loadArtists = useCallback(async () => {
    const mySeq = ++loadSeq.current
    setLoading(true)
    setError(null)
    try {
      const data = await adminApi.catalogArtists({
        ...(kind ? { kind } : {}),
        ...(search.trim() ? { search: search.trim() } : {}),
        limit: 500,
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
    const timer = setTimeout(loadArtists, 300)
    return () => clearTimeout(timer)
  }, [loadArtists])

  const refresh = useCallback(async () => {
    bustCatalogCache()
    setAlbumsByArtist({})
    setSelectedArtists([])
    setSelectedAlbums([])
    setMenu(null)
    await loadArtists()
  }, [loadArtists])

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
    if (!window.confirm(`Удалить «${artist.display_name}»? Возможно только если внутри нет альбомов.`)) return
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

  const moveSelectionTo = async (targetArtistId) => {
    const targetId = Number(targetArtistId)
    if (!targetId) return
    if (selectedArtists.includes(targetId)) {
      setError('Нельзя переместить карточку в саму себя')
      return
    }
    setBusyId('selection')
    try {
      if (selectedAlbums.length) {
        await adminApi.batchAlbums({ album_ids: selectedAlbums, artist_id: targetId })
      }
      if (selectedArtists.length) {
        await adminApi.batchArtists({ artist_ids: selectedArtists, parent_id: targetId })
      }
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось переместить выбранные карточки')
    } finally {
      setBusyId(null)
    }
  }

  const createCollection = async (moveSelection = false) => {
    const name = newName.trim()
    if (!name || creating) return
    setCreating(true)
    setError(null)
    try {
      const created = await adminApi.createCollection({ display_name: name, kind: newKind })
      setNewName('')
      if (moveSelection) {
        setBusyId('selection')
        if (selectedAlbums.length) {
          await adminApi.batchAlbums({ album_ids: selectedAlbums, artist_id: created.id })
        }
        if (selectedArtists.length) {
          await adminApi.batchArtists({ artist_ids: selectedArtists, parent_id: created.id })
        }
      }
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось создать карточку')
    } finally {
      setCreating(false)
      setBusyId(null)
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
      setAlbumsByArtist({})
      await loadArtists()
      if (expanded) loadAlbums(expanded === album.artist_id ? targetArtistId : expanded)
    } catch (e) {
      setError(e.message || 'Не удалось переместить альбом')
    } finally {
      setBusyId(null)
    }
  }

  const scanArtist = async (artist) => {
    setBusyId(`scan-a${artist.id}`)
    setMenu(null)
    try {
      await adminApi.scanArtist(artist.id)
      bustCatalogCache()
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось просканировать карточку')
    } finally {
      setBusyId(null)
    }
  }

  const scanAlbum = async (album, artistId) => {
    setBusyId(`scan-b${album.id}`)
    setMenu(null)
    try {
      await adminApi.scanAlbum(album.id)
      bustCatalogCache()
      await loadAlbums(artistId)
    } catch (e) {
      setError(e.message || 'Не удалось просканировать альбом')
    } finally {
      setBusyId(null)
    }
  }

  const selectAllAlbums = async () => {
    try {
      const data = await adminApi.catalogAlbums({ limit: 500 })
      const ids = (data || []).map((album) => album.id)
      setSelectedAlbums((prev) => prev.length === ids.length ? [] : ids)
    } catch (e) {
      setError(e.message || 'Не удалось выбрать альбомы')
    }
  }

  const updateSelectedArtists = async (locked) => {
    if (!selectedArtists.length) return
    setBusyId('bulk')
    try {
      await adminApi.batchArtists({ artist_ids: selectedArtists, locked })
      await refresh()
    } catch (e) {
      setError(e.message || 'Не удалось изменить выбранные карточки')
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

      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--line)', borderRadius: 12, padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <FolderHeart size={15} style={{ color: 'var(--accent)' }} /> Новая карточка
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Название карточки"
            onKeyDown={(e) => {
              if (e.key === 'Enter') createCollection()
            }}
            style={{ ...inputStyle, flex: 1, minWidth: 180 }}
          />
          <select value={newKind} onChange={(e) => setNewKind(e.target.value)} style={{ ...inputStyle, minHeight: 40 }}>
            {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
          </select>
          <button
            type="button"
            disabled={creating || !newName.trim()}
            onClick={() => createCollection(false)}
            style={{ background: 'var(--accent)', color: '#141210', border: 'none', borderRadius: 8, padding: '8px 14px', minHeight: 40, fontWeight: 800, fontSize: 12, cursor: creating || !newName.trim() ? 'default' : 'pointer', opacity: creating || !newName.trim() ? 0.5 : 1, display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Создать
          </button>
          <button
            type="button"
            disabled={creating || !newName.trim() || (!selectedArtists.length && !selectedAlbums.length)}
            onClick={() => createCollection(true)}
            style={{ background: 'var(--accent-muted)', color: 'var(--accent)', border: '1px solid var(--line)', borderRadius: 8, padding: '8px 12px', minHeight: 40, fontWeight: 800, fontSize: 12, cursor: 'pointer', opacity: !newName.trim() || (!selectedArtists.length && !selectedAlbums.length) ? 0.5 : 1, display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <FolderInput size={14} /> Создать и переместить выбранное
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Поиск по каталогу…" style={{ ...inputStyle, flex: 1, minWidth: 160 }} />
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ ...inputStyle }}>
          <option value="">Все типы</option>
          {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
        </select>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <button type="button" onClick={() => setSelectedArtists((prev) => prev.length === artists.length ? [] : artists.map((item) => item.id))} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>
          {selectedArtists.length === artists.length && artists.length ? 'Снять карточки' : 'Выбрать карточки'}
        </button>
        <button type="button" onClick={selectAllAlbums} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>
          {selectedAlbums.length ? 'Снять альбомы' : 'Выбрать все альбомы'}
        </button>
        <span style={{ fontSize: 12, color: 'var(--dim)' }}>Карточки: {selectedArtists.length} · Альбомы: {selectedAlbums.length}</span>
        <select value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)} style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 220 }}>
          <option value="">Переместить в…</option>
          {artists.map((artist) => <option key={artist.id} value={artist.id}>{artist.display_name}</option>)}
        </select>
        <button type="button" disabled={!moveTarget || (!selectedArtists.length && !selectedAlbums.length) || busyId === 'selection'} onClick={() => moveSelectionTo(moveTarget)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>
          <FolderInput size={13} /> Переместить
        </button>
        <button type="button" disabled={!selectedArtists.length || busyId === 'bulk'} onClick={() => updateSelectedArtists(true)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>Закрепить карточки</button>
        <button type="button" disabled={!selectedArtists.length || busyId === 'bulk'} onClick={() => updateSelectedArtists(false)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>Разблокировать</button>
      </div>

      {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}><Loader2 size={20} className="animate-spin" /> Загрузка…</div>}
      {!loading && artists.length === 0 && <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>Ничего не найдено</div>}

      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {artists.map((artist) => {
          const isOpen = expanded === artist.id
          const albums = albumsByArtist[artist.id] || []
          const isEditing = editingArtist?.id === artist.id
          const busy = busyId === `a${artist.id}`
          return (
            <div
              key={artist.id}
              onClick={() => setMenu(menu?.type === 'artist' && menu.id === artist.id ? null : { type: 'artist', id: artist.id })}
              onDragOver={(e) => {
                if (dragAlbum && dragAlbum.artist_id !== artist.id) {
                  e.preventDefault()
                  setDropTarget(artist.id)
                }
              }}
              onDragLeave={() => setDropTarget((target) => target === artist.id ? null : target)}
              onDrop={(e) => {
                e.preventDefault()
                if (dragAlbum && dragAlbum.artist_id !== artist.id) moveAlbum(dragAlbum, artist.id)
              }}
              style={{ border: '1px solid var(--line)', borderRadius: 10, marginBottom: 8, background: dropTarget === artist.id ? 'var(--accent-muted)' : 'var(--bg-card)', position: 'relative' }}
            >
              <Row dropHl={false}>
                <input type="checkbox" checked={selectedArtists.includes(artist.id)} onClick={(e) => e.stopPropagation()} onChange={(e) => setSelectedArtists((prev) => e.target.checked ? [...prev, artist.id] : prev.filter((id) => id !== artist.id))} aria-label={`Выбрать ${artist.display_name}`} />
                <button type="button" onClick={(e) => { e.stopPropagation(); toggleExpand(artist) }} style={{ ...iconBtn, border: 'none' }} aria-label={isOpen ? 'Свернуть' : 'Развернуть'}>
                  {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                </button>
                {isEditing ? (
                  <>
                    <input value={editingArtist.name} onClick={(e) => e.stopPropagation()} onChange={(e) => setEditingArtist({ id: artist.id, name: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') saveArtistName(artist); if (e.key === 'Escape') setEditingArtist(null) }} autoFocus style={{ ...inputStyle, flex: 1, minWidth: 120 }} />
                    <button type="button" onClick={(e) => { e.stopPropagation(); saveArtistName(artist) }} disabled={busy} style={iconBtn} aria-label="Сохранить"><Check size={15} style={{ color: 'var(--accent)' }} /></button>
                    <button type="button" onClick={(e) => { e.stopPropagation(); setEditingArtist(null) }} style={iconBtn} aria-label="Отмена"><X size={15} /></button>
                  </>
                ) : (
                  <>
                    <div style={{ flex: 1, minWidth: 120 }}>
                      <div style={{ fontWeight: 700, fontSize: 13.5 }}>{artist.display_name}</div>
                      <div style={{ fontSize: 11, color: 'var(--dim)' }}>{KIND_LABEL[artist.kind] || artist.kind} · {artist.album_count ?? 0} альб.{artist.locked ? ' · закреплён' : ''}</div>
                    </div>
                    <select value={artist.kind} disabled={busy} onClick={(e) => e.stopPropagation()} onChange={(e) => changeKind(artist, e.target.value)} title="Тип карточки" style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 150 }}>
                      {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                    </select>
                    <button type="button" onClick={(e) => { e.stopPropagation(); setMenu(menu?.type === 'artist' && menu.id === artist.id ? null : { type: 'artist', id: artist.id }) }} style={iconBtn} title="Действия"><MoreVertical size={15} /></button>
                  </>
                )}
              </Row>

              {menu?.type === 'artist' && menu.id === artist.id && (
                <ActionMenu>
                  <MenuAction icon={Pencil} onClick={() => { setEditingArtist({ id: artist.id, name: artist.display_name }); setMenu(null) }}>Переименовать</MenuAction>
                  {onOpenCoverPicker && <MenuAction icon={ImageIcon} onClick={() => { onOpenCoverPicker({ type: 'artist', id: artist.id, displayName: artist.display_name, hasCustom: !!artist.custom_image_url }); setMenu(null) }}>Изменить обложку</MenuAction>}
                  <MenuAction icon={ScanLine} disabled={busyId === `scan-a${artist.id}`} onClick={() => scanArtist(artist)}>Сканировать</MenuAction>
                  <MenuAction icon={Trash2} danger onClick={() => removeArtist(artist)}>Удалить</MenuAction>
                </ActionMenu>
              )}

              {isOpen && (
                <div style={{ padding: '4px 8px 10px 44px' }}>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, marginBottom: 4 }}>
                    <button type="button" onClick={(e) => { e.stopPropagation(); setSelectedAlbums((prev) => prev.length === albums.length ? prev.filter((id) => !albums.some((album) => album.id === id)) : [...new Set([...prev, ...albums.map((album) => album.id)])]) }} style={{ ...iconBtn, minHeight: 30, padding: '0 8px', fontSize: 11 }}>
                      Выбрать альбомы карточки
                    </button>
                  </div>
                  {albums.length === 0 && <div style={{ fontSize: 12, color: 'var(--dim)', padding: '6px 0' }}>Альбомов нет — перетащите сюда альбом из другой карточки</div>}
                  {albums.map((album) => {
                    const albEditing = editingAlbum?.id === album.id
                    const albBusy = busyId === `b${album.id}`
                    return (
                      <div
                        key={album.id}
                        onClick={(e) => { e.stopPropagation(); setMenu(menu?.type === 'album' && menu.id === album.id ? null : { type: 'album', id: album.id, artistId: artist.id }) }}
                        draggable
                        onDragStart={(e) => { e.stopPropagation(); setDragAlbum(album) }}
                        onDragEnd={() => { setDragAlbum(null); setDropTarget(null) }}
                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 6px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap', opacity: dragAlbum?.id === album.id ? 0.5 : 1 }}
                      >
                        <input type="checkbox" checked={selectedAlbums.includes(album.id)} onClick={(e) => e.stopPropagation()} onChange={(e) => setSelectedAlbums((prev) => e.target.checked ? [...prev, album.id] : prev.filter((id) => id !== album.id))} aria-label={`Выбрать ${album.title}`} />
                        <span title="Перетащите в другую карточку" style={{ color: 'var(--dim)', cursor: 'grab', display: 'inline-flex' }}><GripVertical size={15} /></span>
                        {albEditing ? (
                          <>
                            <input value={editingAlbum.title} onClick={(e) => e.stopPropagation()} onChange={(e) => setEditingAlbum({ ...editingAlbum, title: e.target.value })} onKeyDown={(e) => { if (e.key === 'Enter') saveAlbum(album, artist.id); if (e.key === 'Escape') setEditingAlbum(null) }} autoFocus placeholder="Название альбома" style={{ ...inputStyle, flex: 2, minWidth: 140 }} />
                            <input value={editingAlbum.year} onClick={(e) => e.stopPropagation()} onChange={(e) => setEditingAlbum({ ...editingAlbum, year: e.target.value })} placeholder="Год" inputMode="numeric" style={{ ...inputStyle, width: 84 }} />
                            <button type="button" onClick={(e) => { e.stopPropagation(); saveAlbum(album, artist.id) }} disabled={albBusy} style={iconBtn} aria-label="Сохранить альбом"><Check size={15} style={{ color: 'var(--accent)' }} /></button>
                            <button type="button" onClick={(e) => { e.stopPropagation(); setEditingAlbum(null) }} style={iconBtn} aria-label="Отмена"><X size={15} /></button>
                          </>
                        ) : (
                          <div style={{ flex: 1, minWidth: 140, fontSize: 13 }}><b>{album.title}</b><span style={{ color: 'var(--dim)' }}> · {album.year || 'без года'}</span></div>
                        )}
                        <select value={album.artist_id} disabled={albBusy} onClick={(e) => e.stopPropagation()} onChange={(e) => moveAlbum(album, Number(e.target.value))} title="Переместить в карточку" style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 170 }}>
                          {artists.map((a) => <option key={a.id} value={a.id}>{a.display_name.slice(0, 28)}</option>)}
                        </select>
                      </div>
                    )
                  })}
                </div>
              )}

              {menu?.type === 'album' && menu.id === album.id && menu.artistId === artist.id && (
                <ActionMenu>
                  <MenuAction icon={Pencil} onClick={() => { setEditingAlbum({ id: album.id, title: album.title, year: album.year || '' }); setMenu(null) }}>Переименовать</MenuAction>
                  {onOpenCoverPicker && <MenuAction icon={ImageIcon} onClick={() => { onOpenCoverPicker({ type: 'album', id: album.id, title: album.title, hasCustom: !!album.custom_image_url }); setMenu(null) }}>Изменить обложку</MenuAction>}
                  <MenuAction icon={ScanLine} disabled={busyId === `scan-b${album.id}`} onClick={() => scanAlbum(album, artist.id)}>Сканировать</MenuAction>
                </ActionMenu>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 8 }}>
        Нажмите на карточку или альбом, чтобы открыть меню. Для переноса выберите несколько карточек/альбомов, укажите целевую карточку или создайте новую.
      </div>
    </div>
  )
}
