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
  FolderOpen,
  ArrowLeft,
  House,
  MoreVertical,
  ScanLine,
  FolderInput,
  ListMusic,
} from 'lucide-react'
import { adminApi } from '../api/admin'
import { bustCatalogCache, emitCatalogChanged } from '../api/library'
import { trackRenamePatch } from '../utils/adminEdits'
import { describeScan } from '../utils/scanReport'
import { useScanStore } from '../store/scanStore'
import { useConfirm } from './ui/useConfirm'

const KINDS = [
  { id: 'artist', label: 'Артист' },
  { id: 'compilation', label: 'Сборник' },
  { id: 'standalone_album', label: 'Отдельный релиз' },
  { id: 'genre_collection', label: 'Коллекция' },
]

const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.id, k.label]))

// «Удалить 1 карточку / 3 карточки / 12 карточек».
function cardsWord(n) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'карточку'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'карточки'
  return 'карточек'
}

// Why a card in the selection did not disappear — the backend reports a code
// per skipped card instead of one opaque failure.
const SKIP_REASON = {
  not_found: 'карточка уже удалена',
  refused: 'удаление отклонено',
  error: 'внутренняя ошибка',
}

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
  // Drill-down stack of the cards the admin walked into. The catalog is a tree
  // (artist → album group → release); the list used to ask for every level at
  // once, so album folders — which the nested sync stores as cards — showed up
  // as top-level «Артист» rows next to real artists.
  const [path, setPath] = useState([])
  const currentParentId = path.length ? path[path.length - 1].id : null
  // Flat list for the «Переместить в…» select: targets must not be limited to
  // the level currently on screen.
  const [moveTargets, setMoveTargets] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
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
  // Track level of the catalog: an album's tracks are VFiles, so they are
  // loaded lazily per album and edited through the generic admin file API.
  const [expandedAlbum, setExpandedAlbum] = useState(null)
  const [tracksByAlbum, setTracksByAlbum] = useState({})
  const [tracksLoading, setTracksLoading] = useState(null)
  const [editingTrack, setEditingTrack] = useState(null)
  const [trackBusyId, setTrackBusyId] = useState(null)
  const { confirm, confirmDialog } = useConfirm()
  const loadSeq = useRef(0)

  // `silent` refetches in place without the «Загрузка…» placeholder — used after
  // a mutation that already patched the list, so the rows never flash.
  const loadArtists = useCallback(async (options = {}) => {
    const silent = options?.silent === true
    const mySeq = ++loadSeq.current
    if (!silent) setLoading(true)
    setError(null)
    try {
      // Постраничная догрузка: бэкенд отдаёт максимум 500 за раз,
      // без этого хвост каталога (после буквы Г) просто не виден.
      const PAGE = 500
      const MAX_TOTAL = 20000
      const trimmed = search.trim()
      const base = {
        ...(kind ? { kind } : {}),
        ...(trimmed ? { search: trimmed } : {}),
        // Browsing walks one level at a time (same rule as the public library);
        // a search looks across all levels so nothing becomes unreachable.
        ...(trimmed
          ? {}
          : currentParentId
            ? { parent_id: currentParentId }
            : { root_only: true }),
      }
      let offset = 0
      let all = []
      for (;;) {
        const batch = await adminApi.catalogArtists({ ...base, limit: PAGE, offset })
        const items = batch || []
        all = all.concat(items)
        if (items.length < PAGE || all.length >= MAX_TOTAL) break
        offset += PAGE
      }
      if (mySeq === loadSeq.current) setArtists(all)
    } catch (e) {
      if (mySeq === loadSeq.current) {
        setError(e.message || 'Не удалось загрузить каталог')
        setArtists([])
      }
    } finally {
      if (mySeq === loadSeq.current && !silent) setLoading(false)
    }
  }, [kind, search, currentParentId])

  useEffect(() => {
    // setTimeout would hand the callback its own arguments — call explicitly so
    // the debounced load is never mistaken for a silent one.
    const timer = setTimeout(() => loadArtists(), 300)
    return () => clearTimeout(timer)
  }, [loadArtists])

  // Flat target list for «Переместить в…». Loaded on first focus of the select:
  // the visible level is usually just a handful of cards, and a move must be
  // able to reach a card two levels up.
  const loadMoveTargets = useCallback(async () => {
    if (moveTargets) return
    try {
      const PAGE = 500
      let offset = 0
      let all = []
      for (;;) {
        const batch = await adminApi.catalogArtists({ limit: PAGE, offset })
        const items = batch || []
        all = all.concat(items)
        if (items.length < PAGE || all.length >= 20000) break
        offset += PAGE
      }
      setMoveTargets(all)
    } catch (e) {
      setError(e.message || 'Не удалось загрузить список карточек')
      setMoveTargets([])
    }
  }, [moveTargets])

  const resetLevelState = () => {
    setExpanded(null)
    setMenu(null)
    setSelectedArtists([])
    setSelectedAlbums([])
    setEditingArtist(null)
    setEditingAlbum(null)
    setExpandedAlbum(null)
    setTracksByAlbum({})
    setNotice(null)
  }

  const drillInto = (artist) => {
    if (!(artist.children_count > 0)) return
    resetLevelState()
    setPath((prev) => [...prev, { id: artist.id, display_name: artist.display_name }])
  }

  // index -1 — the catalog root.
  const goToLevel = (index) => {
    if (index === path.length - 1) return
    resetLevelState()
    setPath((prev) => prev.slice(0, index + 1))
  }

  // The cover picker lives outside this component (App.jsx), so without a
  // listener a saved cover or rename never reached this list: the row kept
  // showing the previous artwork and the action read as if it had done nothing
  // at all. Reload the data but keep the expansion/selection state intact —
  // `refresh()` would collapse an expanded artist mid-edit.
  useEffect(() => {
    const onChanged = (event) => {
      // Bulk delete patches this list and refetches it itself; the event is
      // only for the views behind the panel. Reloading here would flash the
      // list twice.
      if (event?.detail?.action === 'cards-deleted') return
      loadArtists()
      setMoveTargets(null)
      for (const artistId of Object.keys(albumsByArtist)) {
        loadAlbums(Number(artistId))
      }
    }
    window.addEventListener('tmusic:catalog-changed', onChanged)
    return () => window.removeEventListener('tmusic:catalog-changed', onChanged)
  }, [loadArtists, albumsByArtist])

  const refresh = useCallback(async () => {
    bustCatalogCache()
    setAlbumsByArtist({})
    setSelectedArtists([])
    setSelectedAlbums([])
    setMenu(null)
    setMoveTargets(null)
    // Track caches are keyed by album id — drop them so a rename/move cannot
    // leave a stale row behind in an expanded album.
    setTracksByAlbum({})
    setExpandedAlbum(null)
    setEditingTrack(null)
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

  // ─── Track level ──────────────────────────────────────────────────────────
  const loadAlbumTracks = async (album) => {
    if (!album.folder_id) {
      setTracksByAlbum((prev) => ({ ...prev, [album.id]: [] }))
      return
    }
    setTracksLoading(album.id)
    try {
      const data = await adminApi.vfsFiles({ folder_id: album.folder_id, limit: 500 })
      setTracksByAlbum((prev) => ({ ...prev, [album.id]: data?.items || [] }))
    } catch (e) {
      setError(e.message || 'Не удалось загрузить треки')
      setTracksByAlbum((prev) => ({ ...prev, [album.id]: [] }))
    } finally {
      setTracksLoading(null)
    }
  }

  const toggleAlbumTracks = (album) => {
    if (expandedAlbum === album.id) {
      setExpandedAlbum(null)
      return
    }
    setExpandedAlbum(album.id)
    if (!tracksByAlbum[album.id]) loadAlbumTracks(album)
  }

  const saveTrackName = async (album, track) => {
    const body = trackRenamePatch(editingTrack?.draft, track.name)
    if (!body) {
      setEditingTrack(null)
      return
    }
    setTrackBusyId(track.id)
    try {
      await adminApi.patchVfsFile(track.id, body)
      setTracksByAlbum((prev) => ({
        ...prev,
        [album.id]: (prev[album.id] || []).map((t) => (t.id === track.id ? { ...t, name: body.name } : t)),
      }))
      setEditingTrack(null)
      bustCatalogCache()
    } catch (e) {
      setError(e.message || 'Не удалось переименовать трек')
    } finally {
      setTrackBusyId(null)
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
    const ok = await confirm({
      title: 'Удалить карточку',
      message:
        `Удалить «${artist.display_name}» целиком? Вместе с карточкой удалятся её альбомы. ` +
        'Аудиофайлы остаются в файловом дереве — карточку можно вернуть сканированием.',
      danger: true,
      confirmLabel: 'Удалить',
    })
    if (!ok) return
    setBusyId(`a${artist.id}`)
    setError(null)
    setNotice(null)
    try {
      const result = await adminApi.deleteArtist(artist.id)
      // Same immediate update as the bulk path: the row goes on the response.
      setArtists((prev) => prev.filter((card) => card.id !== artist.id))
      setAlbumsByArtist((prev) => {
        const next = { ...prev }
        delete next[artist.id]
        return next
      })
      setTracksByAlbum((prev) => {
        const next = { ...prev }
        delete next[artist.id]
        return next
      })
      if (expanded === artist.id) setExpanded(null)
      setExpandedAlbum(null)
      setEditingArtist((prev) => (prev && prev.id === artist.id ? null : prev))
      setSelectedArtists((prev) => prev.filter((id) => id !== artist.id))
      setMoveTargets(null)
      bustCatalogCache()
      await loadArtists({ silent: true })
      emitCatalogChanged({ action: 'cards-deleted', ids: [artist.id] })
      setNotice({
        kind: 'ok',
        text:
          `«${artist.display_name}» удалена` +
          (result?.deleted_albums ? ` вместе с альбомами (${result.deleted_albums})` : '') +
          '.',
      })
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
      const created = await adminApi.createCollection({
        display_name: name,
        kind: newKind,
        // The new card lands on the level the admin is looking at, not at the
        // root — creating «внутри сборника» is the common case.
        ...(currentParentId ? { parent_id: currentParentId } : {}),
      })
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
    setNotice(null)
    try {
      // The scan is a background job now: this only starts it. Progress and the
      // final report are shown by ScanProgressHost (top-left), which also
      // broadcasts `tmusic:catalog-changed` when the job commits.
      const started = await useScanStore.getState().startArtistScan(artist.id, artist.display_name)
      if (!started) {
        setNotice({ kind: 'muted', text: `«${artist.display_name}»: сканирование не запущено.` })
      }
    } catch (e) {
      setError(e.message || 'Не удалось просканировать карточку')
    } finally {
      setBusyId(null)
    }
  }

  const scanAlbum = async (album, artistId) => {
    setBusyId(`scan-b${album.id}`)
    setMenu(null)
    setNotice(null)
    try {
      const report = await adminApi.scanAlbum(album.id)
      bustCatalogCache()
      await loadAlbums(artistId)
      setNotice({
        kind: report?.updated ? 'ok' : 'muted',
        text: `«${album.title}»: ${describeScan(report)}`,
      })
    } catch (e) {
      setError(e.message || 'Не удалось просканировать альбом')
    } finally {
      setBusyId(null)
    }
  }

  const selectAllAlbums = async () => {
    try {
      const PAGE = 500
      let offset = 0
      let all = []
      for (;;) {
        const batch = await adminApi.catalogAlbums({ limit: PAGE, offset })
        const items = batch || []
        all = all.concat(items)
        if (items.length < PAGE || all.length >= 20000) break
        offset += PAGE
      }
      const ids = all.map((album) => album.id)
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

  // Delete every checked card in one go. The card goes entirely — its albums
  // are deleted with it; audio files stay in the VFS.
  const deleteSelectedArtists = async () => {
    const ids = [...selectedArtists]
    if (!ids.length || busyId === 'bulk-delete') return
    const ok = await confirm({
      title: 'Удалить выбранные карточки',
      message:
        `Удалить ${ids.length} ${cardsWord(ids.length)} целиком? Вместе с карточками удалятся их альбомы. ` +
        'Аудиофайлы остаются в файловом дереве. Вложенные карточки не удаляются: они открепляются и остаются в каталоге.',
      danger: true,
      confirmLabel: 'Удалить',
    })
    if (!ok) return

    setBusyId('bulk-delete')
    setError(null)
    setNotice(null)
    try {
      const result = await adminApi.batchDeleteArtists({ artist_ids: ids })
      const deletedIds = new Set(result?.deleted || [])
      const skipped = result?.skipped || []

      // Patch the visible state first: the rows must disappear on the response,
      // not one refetch later.
      setArtists((prev) => prev.filter((card) => !deletedIds.has(card.id)))
      setAlbumsByArtist((prev) => {
        const next = { ...prev }
        for (const id of deletedIds) delete next[id]
        return next
      })
      setTracksByAlbum((prev) => {
        const next = { ...prev }
        for (const id of deletedIds) delete next[id]
        return next
      })
      setExpanded((prev) => (prev && deletedIds.has(prev) ? null : prev))
      setExpandedAlbum((prev) => (prev && deletedIds.has(prev) ? null : prev))
      setEditingArtist((prev) => (prev && deletedIds.has(prev.id) ? null : prev))
      setSelectedArtists((prev) => prev.filter((id) => !deletedIds.has(id)))
      setMoveTargets(null)
      bustCatalogCache()

      // Refetch the level (counts of parents, new neighbours), then tell the
      // library grid and the album sheet behind the panel to drop the cards.
      await loadArtists({ silent: true })
      emitCatalogChanged({ action: 'cards-deleted', ids: [...deletedIds] })

      const reasons = [...new Set(skipped.map((item) => SKIP_REASON[item.code] || SKIP_REASON.error))]
      const albums = result?.deleted_albums || 0
      setNotice({
        kind: skipped.length ? 'muted' : 'ok',
        text:
          `Удалено карточек: ${deletedIds.size}` +
          (albums ? ` (альбомов: ${albums})` : '') +
          '.' +
          (skipped.length ? ` Пропущено: ${skipped.length} (${reasons.join(', ')}).` : ''),
      })
    } catch (e) {
      setError(e.message || 'Не удалось удалить выбранные карточки')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      {confirmDialog}
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

      {notice && (
        <div
          role="status"
          style={{
            marginBottom: 12,
            padding: '10px 12px',
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            // 'muted' means the scan ran but Last.fm had nothing new — it must
            // not look like a success.
            background: notice.kind === 'muted' ? 'var(--bg)' : 'var(--accent-muted)',
            color: notice.kind === 'muted' ? 'var(--dim)' : 'var(--accent)',
            border: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <ScanLine size={14} /> {notice.text}
          <button type="button" onClick={() => setNotice(null)} style={{ ...iconBtn, marginLeft: 'auto' }} aria-label="Закрыть">
            <X size={14} />
          </button>
        </div>
      )}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }} data-testid="catalog-breadcrumbs">
        <button
          type="button"
          onClick={() => goToLevel(-1)}
          style={{ ...iconBtn, minHeight: 32, padding: '0 10px', fontSize: 12, gap: 6, width: 'auto', fontWeight: path.length ? 600 : 800, color: path.length ? 'var(--dim)' : 'var(--ink)' }}
          aria-label="Верхний уровень каталога"
        >
          <House size={13} /> Каталог
        </button>
        {path.map((node, index) => (
          <React.Fragment key={node.id}>
            <ChevronRight size={13} style={{ color: 'var(--dim)', flexShrink: 0 }} />
            <button
              type="button"
              onClick={() => goToLevel(index)}
              style={{ ...iconBtn, minHeight: 32, padding: '0 10px', fontSize: 12, width: 'auto', fontWeight: index === path.length - 1 ? 800 : 600, color: index === path.length - 1 ? 'var(--ink)' : 'var(--dim)' }}
              title={node.display_name}
            >
              {node.display_name}
            </button>
          </React.Fragment>
        ))}
        {path.length > 0 && (
          <button
            type="button"
            onClick={() => goToLevel(path.length - 2)}
            style={{ ...iconBtn, minHeight: 32, padding: '0 10px', fontSize: 12, width: 'auto', gap: 6, marginLeft: 'auto' }}
            aria-label="На уровень выше"
          >
            <ArrowLeft size={13} /> Назад
          </button>
        )}
      </div>

      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--line)', borderRadius: 12, padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
          <FolderHeart size={15} style={{ color: 'var(--accent)' }} /> Новая карточка
          <span style={{ fontWeight: 600, fontSize: 11, color: 'var(--dim)' }}>
            {path.length ? `внутри «${path[path.length - 1].display_name}»` : 'на верхнем уровне каталога'}
          </span>
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
            style={{ background: 'var(--accent)', color: 'var(--on-accent)', border: 'none', borderRadius: 8, padding: '8px 14px', minHeight: 40, fontWeight: 800, fontSize: 12, cursor: creating || !newName.trim() ? 'default' : 'pointer', opacity: creating || !newName.trim() ? 0.5 : 1, display: 'inline-flex', alignItems: 'center', gap: 6 }}
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
        <select
          value={moveTarget}
          onChange={(e) => setMoveTarget(e.target.value)}
          onFocus={loadMoveTargets}
          style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 220 }}
        >
          <option value="">Переместить в…</option>
          {(moveTargets || artists).map((artist) => (
            <option key={artist.id} value={artist.id}>
              {artist.parent_name ? `${artist.parent_name} › ${artist.display_name}` : artist.display_name}
            </option>
          ))}
        </select>
        <button type="button" disabled={!moveTarget || (!selectedArtists.length && !selectedAlbums.length) || busyId === 'selection'} onClick={() => moveSelectionTo(moveTarget)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>
          <FolderInput size={13} /> Переместить
        </button>
        <button type="button" disabled={!selectedArtists.length || busyId === 'bulk'} onClick={() => updateSelectedArtists(true)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>Закрепить карточки</button>
        <button type="button" disabled={!selectedArtists.length || busyId === 'bulk'} onClick={() => updateSelectedArtists(false)} style={{ ...iconBtn, minHeight: 36, padding: '0 10px', fontSize: 12 }}>Разблокировать</button>
        <button
          type="button"
          disabled={!selectedArtists.length || busyId === 'bulk-delete'}
          onClick={deleteSelectedArtists}
          style={{
            ...iconBtn,
            minHeight: 36,
            padding: '0 10px',
            fontSize: 12,
            gap: 6,
            width: 'auto',
            color: 'var(--danger)',
            borderColor: 'var(--danger)',
            cursor: selectedArtists.length ? 'pointer' : 'default',
            opacity: selectedArtists.length ? 1 : 0.5,
          }}
          title="Удалить отмеченные карточки"
          aria-label={
            selectedArtists.length
              ? `Удалить выбранные карточки (${selectedArtists.length})`
              : 'Удалить выбранные карточки'
          }
        >
          {busyId === 'bulk-delete'
            ? <Loader2 size={13} className="animate-spin" />
            : <Trash2 size={13} />}
          Удалить выбранные{selectedArtists.length ? ` (${selectedArtists.length})` : ''}
        </button>
      </div>

      {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}><Loader2 size={20} className="animate-spin" /> Загрузка…</div>}
      {!loading && artists.length > 0 && (
        <div style={{ fontSize: 11.5, color: 'var(--dim)', marginBottom: 8 }}>
          {search.trim() ? (
            <>Найдено карточек по всему каталогу: <b>{artists.length}</b></>
          ) : (
            <>
              Карточек на этом уровне: <b>{artists.length}</b>
              {path.length ? ` · внутри «${path[path.length - 1].display_name}»` : ' · верхний уровень каталога'}
            </>
          )}
        </div>
      )}
      {!loading && artists.length === 0 && (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--dim)', fontSize: 13 }}>
          {path.length ? 'Внутри этой карточки пока ничего нет' : 'Ничего не найдено'}
        </div>
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
                      <div style={{ fontSize: 11, color: 'var(--dim)' }}>
                        {KIND_LABEL[artist.kind] || artist.kind} · {artist.album_count ?? 0} альб.
                        {artist.children_count > 0 ? ` · ${artist.children_count} влож.` : ''}
                        {/* Search results span every level — say which card they belong to. */}
                        {artist.parent_id && artist.parent_name ? ` · ↳ ${artist.parent_name}` : ''}
                        {artist.locked ? ' · закреплён' : ''}
                      </div>
                    </div>
                    <select value={artist.kind} disabled={busy} onClick={(e) => e.stopPropagation()} onChange={(e) => changeKind(artist, e.target.value)} title="Тип карточки" aria-label={`Тип карточки: ${artist.display_name}`} style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 150 }}>
                      {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                    </select>
                    {artist.children_count > 0 && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); drillInto(artist) }}
                        style={iconBtn}
                        title={`Открыть вложенные карточки (${artist.children_count})`}
                        aria-label={`Открыть вложенные карточки: ${artist.display_name}`}
                      >
                        <FolderOpen size={15} />
                      </button>
                    )}
                    {/* Cover art is admin curation, not a rare action — it used
                        to live only inside the ⋮ menu, which made «сменить
                        обложку» effectively undiscoverable. */}
                    {onOpenCoverPicker && (
                      <button type="button" onClick={(e) => { e.stopPropagation(); onOpenCoverPicker({ type: 'artist', id: artist.id, displayName: artist.display_name, hasCustom: !!artist.custom_image_url }) }} style={iconBtn} title="Изменить обложку" aria-label={`Изменить обложку: ${artist.display_name}`}>
                        <ImageIcon size={15} />
                      </button>
                    )}
                    <button type="button" onClick={(e) => { e.stopPropagation(); setMenu(menu?.type === 'artist' && menu.id === artist.id ? null : { type: 'artist', id: artist.id }) }} style={iconBtn} title="Действия" aria-label={`Действия: ${artist.display_name}`}><MoreVertical size={15} /></button>
                  </>
                )}
              </Row>

              {menu?.type === 'artist' && menu.id === artist.id && (
                <ActionMenu>
                  {artist.children_count > 0 && (
                    <MenuAction icon={FolderOpen} onClick={() => { drillInto(artist); setMenu(null) }}>
                      Открыть вложенные ({artist.children_count})
                    </MenuAction>
                  )}
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
                      <React.Fragment key={album.id}>
                        <div
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
                          <select value={album.artist_id} disabled={albBusy} onClick={(e) => e.stopPropagation()} onChange={async (e) => {
                          const toId = Number(e.target.value)
                          // UI-41: revert instantly if the user backs out — a stray
                          // click must not silently move the album between cards.
                          if (toId === album.artist_id) return
                          const target = artists.find((a) => a.id === toId)
                          const ok = await confirm({
                            title: 'Переместить альбом',
                            message: `Переместить «${album.title}» в карточку «${target?.display_name || ''}»?`,
                            confirmLabel: 'Переместить',
                          })
                          if (!ok) {
                            e.target.value = String(album.artist_id)
                            return
                          }
                          moveAlbum(album, toId)
                        }} title="Переместить в карточку" aria-label={`Переместить «${album.title}» в другую карточку`} style={{ ...inputStyle, minHeight: 36, fontSize: 12, maxWidth: 170 }}>
                            {artists.map((a) => <option key={a.id} value={a.id}>{a.display_name.slice(0, 28)}</option>)}
                          </select>
                          {/* UI-04: keyboard-reachable trigger for the album menu (the
                              row itself contains form controls and can't be a button). */}
                          {onOpenCoverPicker && (
                            <button type="button" onClick={(e) => { e.stopPropagation(); onOpenCoverPicker({ type: 'album', id: album.id, title: album.title, hasCustom: !!album.custom_image_url }) }} style={iconBtn} title="Изменить обложку" aria-label={`Изменить обложку альбома ${album.title}`}>
                              <ImageIcon size={15} />
                            </button>
                          )}
                          <button type="button" onClick={(e) => { e.stopPropagation(); setMenu(menu?.type === 'album' && menu.id === album.id ? null : { type: 'album', id: album.id, artistId: artist.id }) }} style={iconBtn} title="Действия" aria-label={`Действия: ${album.title}`}><MoreVertical size={15} /></button>
                        </div>
                        {menu?.type === 'album' && menu.id === album.id && menu.artistId === artist.id && (
                          <ActionMenu>
                            <MenuAction icon={Pencil} onClick={() => { setEditingAlbum({ id: album.id, title: album.title, year: album.year || '' }); setMenu(null) }}>Переименовать</MenuAction>
                            {onOpenCoverPicker && <MenuAction icon={ImageIcon} onClick={() => { onOpenCoverPicker({ type: 'album', id: album.id, title: album.title, hasCustom: !!album.custom_image_url }); setMenu(null) }}>Изменить обложку</MenuAction>}
                            <MenuAction icon={ScanLine} disabled={busyId === `scan-b${album.id}`} onClick={() => scanAlbum(album, artist.id)}>Сканировать</MenuAction>
                            <MenuAction icon={ListMusic} onClick={() => { toggleAlbumTracks(album); setMenu(null) }}>
                              {expandedAlbum === album.id ? 'Скрыть треки' : 'Треки'}
                            </MenuAction>
                          </ActionMenu>
                        )}
                        {expandedAlbum === album.id && (
                          <div style={{ margin: '0 8px 8px 44px', border: '1px solid var(--line)', borderRadius: 8, background: 'var(--bg)', padding: '6px 8px' }}>
                            {tracksLoading === album.id && (
                              <div style={{ fontSize: 12, color: 'var(--dim)', padding: '6px 0', display: 'flex', alignItems: 'center', gap: 6 }}>
                                <Loader2 size={13} className="animate-spin" /> Загрузка треков…
                              </div>
                            )}
                            {tracksLoading !== album.id && (tracksByAlbum[album.id] || []).length === 0 && (
                              <div style={{ fontSize: 12, color: 'var(--dim)', padding: '6px 0' }}>Треков не найдено</div>
                            )}
                            {(tracksByAlbum[album.id] || []).map((track) => {
                              const trEditing = editingTrack?.id === track.id
                              const trBusy = trackBusyId === track.id
                              return (
                                <div
                                  key={track.id}
                                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 2px', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}
                                >
                                  {track.custom_image_url ? (
                                    <img
                                      src={track.custom_image_url}
                                      alt=""
                                      aria-hidden="true"
                                      style={{ width: 26, height: 26, borderRadius: 5, objectFit: 'cover', border: '1px solid var(--line)', flexShrink: 0 }}
                                    />
                                  ) : (
                                    <span style={{ width: 26, height: 26, borderRadius: 5, border: '1px dashed var(--line)', flexShrink: 0 }} />
                                  )}
                                  {trEditing ? (
                                    <>
                                      <input
                                        value={editingTrack.draft}
                                        onChange={(e) => setEditingTrack({ id: track.id, draft: e.target.value })}
                                        onKeyDown={(e) => {
                                          if (e.key === 'Enter') saveTrackName(album, track)
                                          if (e.key === 'Escape') setEditingTrack(null)
                                        }}
                                        autoFocus
                                        aria-label={`Новое название трека ${track.name}`}
                                        style={{ ...inputStyle, flex: 1, minWidth: 140 }}
                                      />
                                      <button type="button" onClick={() => saveTrackName(album, track)} disabled={trBusy} style={iconBtn} aria-label="Сохранить трек">
                                        {trBusy ? <Loader2 size={14} className="animate-spin" /> : <Check size={15} style={{ color: 'var(--accent)' }} />}
                                      </button>
                                      <button type="button" onClick={() => setEditingTrack(null)} style={iconBtn} aria-label="Отмена"><X size={15} /></button>
                                    </>
                                  ) : (
                                    <>
                                      <span style={{ flex: 1, minWidth: 140, fontSize: 12.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{track.name}</span>
                                      <button type="button" onClick={() => setEditingTrack({ id: track.id, draft: track.name })} style={iconBtn} title="Переименовать трек" aria-label={`Переименовать ${track.name}`}><Pencil size={14} /></button>
                                      {onOpenCoverPicker && (
                                        <button
                                          type="button"
                                          onClick={() => onOpenCoverPicker({ type: 'track', id: track.id, title: track.name, hasCustom: !!track.custom_image_url })}
                                          style={iconBtn}
                                          title="Обложка трека"
                                          aria-label={`Обложка трека ${track.name}`}
                                        >
                                          <ImageIcon size={14} />
                                        </button>
                                      )}
                                    </>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </React.Fragment>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--dim)', marginTop: 8 }}>
        Список показывает один уровень каталога: кнопка <FolderOpen size={12} style={{ verticalAlign: '-2px' }} /> открывает вложенные карточки, «Назад» возвращает на уровень выше. Нажмите на карточку или альбом, чтобы открыть меню. Для переноса выберите несколько карточек/альбомов, укажите целевую карточку или создайте новую; «Удалить выбранные» убирает отмеченные карточки разом. В меню альбома пункт «Треки» открывает список треков: там можно переименовать любой трек и задать ему отдельную обложку.
      </div>
    </div>
  )
}
