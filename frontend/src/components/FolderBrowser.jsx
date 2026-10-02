/**
 * Windows-style VFS folder tree.
 * «+» / «›» слева — expand/collapse вложенных папок.
 * Клик по строке папки — в плейлист + воспроизведение.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  Loader2,
  Music,
  Play,
  Pause,
  Plus,
  Minus,
  HardDrive,
  ListStart,
  ListEnd,
  Check,
} from 'lucide-react'
import { vfsApi } from '../api/vfs'
import { bustMediaCaches } from '../api/cache'
import { playlistsApi } from '../api/playlists'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'
import { useOfflineStore } from '../store/offlineStore'

function fmtDur(sec) {
  if (!sec || isNaN(sec)) return ''
  const s = Math.max(0, Math.floor(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function Expander({ expanded, loading, hasChildren, onClick, size = 26 }) {
  return (
    <button
      type="button"
      className="tmusic-tree-expander"
      onClick={onClick}
      title={expanded ? 'Свернуть' : 'Развернуть'}
      aria-label={expanded ? 'Свернуть папку' : 'Развернуть папку'}
      style={{
        width: size,
        height: size,
        minWidth: size,
        borderRadius: 6,
        border: '1px solid var(--line)',
        background: expanded ? 'var(--accent-muted)' : 'var(--bg-card)',
        color: hasChildren || !expanded ? 'var(--accent)' : 'var(--dim)',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: hasChildren ? 'pointer' : 'default',
        opacity: hasChildren ? 1 : 0.35,
        flexShrink: 0,
        padding: 0,
      }}
    >
      {loading ? (
        <Loader2 size={12} className="animate-spin" />
      ) : expanded ? (
        <Minus size={12} />
      ) : (
        <Plus size={12} />
      )}
    </button>
  )
}

function TreeRow({
  node,
  depth,
  expandedIds,
  childrenMap,
  loadingIds,
  onToggle,
  onPlayFolder,
  onPlayTrack,
  onPlayNext,
  onEnqueue,
  playingId,
  currentTrack,
  isPlaying,
  onTogglePlayCurrent,
}) {
  const isFolder = node.type === 'folder'
  const f = isFolder ? node.folder : null
  const t = !isFolder ? node.track : null
  const id = isFolder ? `f-${f.id}` : `t-${t.id}`
  const expanded = expandedIds.has(id)
  const loading = loadingIds.has(id)
  const kids = childrenMap[id] || null
  const hasKids = isFolder
    ? (f.subfolder_count || 0) > 0 || (f.recursive_track_count || 0) > 0 || (kids && kids.length > 0)
    : false
  const isCurrentTrack = !isFolder && currentTrack?.id === t.id
  const cachedTrackIds = useOfflineStore((s) => s.cachedTrackIds)
  const cacheTrack = useOfflineStore((s) => s.cacheTrack)
  const removeTrack = useOfflineStore((s) => s.removeTrack)
  const isCached = !isFolder && cachedTrackIds.has(Number(t?.id))

  return (
    <>
      <div
        className="tmusic-track-row tmusic-tree-row"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 8px',
          borderRadius: '8px',
          cursor: 'pointer',
          paddingLeft: `calc(6px + ${depth} * var(--tree-indent, 18px))`,
          background:
            (isFolder && playingId === f.id) || isCurrentTrack ? 'var(--accent-muted)' : 'transparent',
          minHeight: 44,
        }}
        onClick={isFolder ? () => onPlayFolder(f) : () => (isCurrentTrack ? onTogglePlayCurrent() : onPlayTrack(t))}
        title={isFolder ? 'Клик — в плейлист и воспроизведение' : 'Играть трек'}
      >
        {isFolder ? (
          <Expander
            expanded={expanded}
            loading={loading}
            hasChildren={hasKids}
            onClick={(e) => {
              e.stopPropagation()
              onToggle(id, f, true)
            }}
          />
        ) : (
          <span
            style={{
              width: 22,
              minWidth: 22,
              display: 'inline-flex',
              justifyContent: 'center',
              color: isCurrentTrack ? 'var(--accent)' : 'var(--dim)',
            }}
          >
            {isCurrentTrack ? (
              isPlaying ? (
                <Pause size={14} fill="var(--accent)" />
              ) : (
                <Play size={14} fill="var(--accent)" />
              )
            ) : (
              <Music size={14} />
            )}
          </span>
        )}

        {isFolder ? (
          expanded ? (
            <FolderOpen size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          ) : (
            <Folder size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          )
        ) : (
          <span style={{ width: 16 }} />
        )}

        <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontWeight: isFolder ? 650 : isCurrentTrack ? 700 : 500,
                fontSize: 13.5,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                color: isCurrentTrack ? 'var(--accent)' : 'var(--ink)',
              }}
            >
              {isFolder ? f.name : (t.name || '').replace(/\.[^/.]+$/, '')}
            </div>
            {isFolder && (
              <div style={{ fontSize: '11px', color: 'var(--dim)' }}>
                {f.subfolder_count > 0 && `${f.subfolder_count} папок · `}
                {f.recursive_track_count || f.track_count || 0} трек(ов)
              </div>
            )}
          </div>
          {!isFolder && isCached && (
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

        {!isFolder && fmtDur(t.duration) && (
          <span style={{ fontSize: '11px', color: 'var(--dim)', flexShrink: 0 }}>
            {fmtDur(t.duration)}
          </span>
        )}
        {!isFolder && (
          <span className="tm-queue-actions" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="tm-queue-mini"
              onClick={() => (isCached ? removeTrack(t.id) : cacheTrack(t))}
              aria-label={isCached ? 'Удалить из кэша' : 'Сохранить в кэш'}
              title={isCached ? 'Удалить из кэша' : 'Сохранить в кэш'}
              style={{ color: isCached ? 'var(--success)' : 'var(--dim)' }}
            >
              <HardDrive size={13} />
            </button>
            <button
              type="button"
              className="tm-queue-mini"
              onClick={() => onPlayNext(t)}
              aria-label="Играть следующим"
              title="Играть следующим"
            >
              <ListStart size={13} />
            </button>
            <button
              type="button"
              className="tm-queue-mini"
              onClick={() => onEnqueue(t)}
              aria-label="Добавить в очередь"
              title="Добавить в очередь"
            >
              <ListEnd size={13} />
            </button>
          </span>
        )}

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (isFolder) onPlayFolder(f)
            else onPlayTrack(t)
          }}
          title={isFolder ? 'В плейлист + играть' : 'Играть трек'}
          style={{
            width: 36,
            height: 36,
            borderRadius: '50%',
            border: 'none',
            background: 'var(--accent)',
            color: 'var(--on-accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          {isFolder && playingId === f.id ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Play size={12} fill="currentColor" />
          )}
        </button>
      </div>

      {isFolder && expanded && kids && kids.length > 0 && (
        <div className="tmusic-tree-children">
          {kids.map((child) => (
            <TreeRow
              key={child.type === 'folder' ? `f-${child.folder.id}` : `t-${child.track.id}`}
              node={child}
              depth={depth + 1}
              expandedIds={expandedIds}
              childrenMap={childrenMap}
              loadingIds={loadingIds}
              onToggle={onToggle}
              onPlayFolder={onPlayFolder}
              onPlayTrack={onPlayTrack}
              onPlayNext={onPlayNext}
              onEnqueue={onEnqueue}
              playingId={playingId}
              currentTrack={currentTrack}
              isPlaying={isPlaying}
              onTogglePlayCurrent={onTogglePlayCurrent}
            />
          ))}
        </div>
      )}
      {isFolder && expanded && kids && kids.length === 0 && !loading && (
        <div
          className="tmusic-tree-empty"
          style={{
            paddingLeft: `calc(34px + ${(depth + 1)} * var(--tree-indent, 18px))`,
            fontSize: '12px',
            color: 'var(--dim)',
            padding: '4px 8px 8px',
          }}
        >
          Папка пуста
        </div>
      )}
    </>
  )
}

export function FolderBrowser() {
  const [roots, setRoots] = useState([])
  const [rootsLoading, setRootsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedIds, setExpandedIds] = useState(() => new Set())
  const [childrenMap, setChildrenMap] = useState({})
  const [loadingIds, setLoadingIds] = useState(() => new Set())
  const [playingId, setPlayingId] = useState(null)
  const { playQueue, playNext, addToQueue, setNotice, currentTrack, isPlaying, togglePlay } = usePlayerStore()
  const { isAuthenticated } = useAuthStore()
  const reqSeq = useRef(0)

  const loadRoots = useCallback(async () => {
    setRootsLoading(true)
    setError(null)
    try {
      const list = await vfsApi.roots()
      setRoots((list || []).map((f) => ({ type: 'folder', folder: f })))
    } catch (e) {
      console.error(e)
      setError('Не удалось загрузить папки')
      setRoots([])
    } finally {
      setRootsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRoots()
  }, [loadRoots])

  useEffect(() => {
    const handleVfsImported = () => {
      // Idempotent: MusicLibrary's own listener busts too, but do not depend
      // on listener order — the tree must refetch from the network here.
      bustMediaCaches()
      setExpandedIds(new Set())
      setChildrenMap({})
      loadRoots()
    }
    window.addEventListener('tmusic:vfs-imported', handleVfsImported)
    return () => window.removeEventListener('tmusic:vfs-imported', handleVfsImported)
  }, [loadRoots])

  const loadChildren = useCallback(async (folderId, nodeId) => {
    const data = await vfsApi.children(folderId)
    const folders = (data?.subfolders || []).map((f) => ({ type: 'folder', folder: f }))
    const tracks = (data?.tracks || []).map((t) => ({ type: 'track', track: t }))
    return [...folders, ...tracks]
  }, [])

  const onToggle = useCallback(
    async (nodeId, folder) => {
      const already = expandedIds.has(nodeId)
      const next = new Set(expandedIds)
      if (already) {
        next.delete(nodeId)
        setExpandedIds(next)
        return
      }
      next.add(nodeId)
      setExpandedIds(next)

      if (childrenMap[nodeId]) return

      setLoadingIds((prev) => new Set(prev).add(nodeId))
      const my = ++reqSeq.current
      try {
        const kids = await loadChildren(folder.id, nodeId)
        if (my === reqSeq.current) {
          setChildrenMap((prev) => ({ ...prev, [nodeId]: kids }))
        }
      } catch (e) {
        console.error(e)
        setChildrenMap((prev) => {
          const nextMap = { ...prev }
          delete nextMap[nodeId]
          return nextMap
        })
        const collapsed = new Set(next)
        collapsed.delete(nodeId)
        setExpandedIds(collapsed)
      } finally {
        setLoadingIds((prev) => {
          const s = new Set(prev)
          s.delete(nodeId)
          return s
        })
      }
    },
    [expandedIds, childrenMap, loadChildren]
  )

  const onPlayTrack = (t) => {
    playQueue([t], 0, {
      title: (t.name || '').replace(/\.[^/.]+$/, ''),
      artistName: 'Файл',
      coverUrl: null,
    })
  }

  // Tapping the already-playing row in the tree toggles play/pause.
  const onTogglePlayCurrent = () => togglePlay()

  const onPlayNext = (t) => {
    playNext(t)
    setNotice({ kind: 'info', text: `«${(t.name || '').replace(/\.[^/.]+$/, '')}» — следующим` })
  }

  const onEnqueue = (t) => {
    addToQueue(t)
    setNotice({ kind: 'info', text: `«${(t.name || '').replace(/\.[^/.]+$/, '')}» — добавлен в очередь` })
  }

  const onPlayFolder = async (folder) => {
    if (playingId) return
    setPlayingId(folder.id)
    try {
      const tracks = await vfsApi.tracks(folder.id, true)
      if (!tracks || tracks.length === 0) return
      playQueue(tracks, 0, {
        title: folder.name,
        artistName: 'Папка',
        coverUrl: null,
      })
      if (isAuthenticated) {
        try {
          const ids = tracks.slice(0, 500).map((t) => t.id)
          await playlistsApi.mirror(folder.name, ids)
        } catch (e) {
          console.error('Playlist mirror failed:', e)
          setNotice({ kind: 'error', text: 'Не удалось подтвердить сохранение плейлиста. Обновите список и попробуйте снова.' })
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
          marginBottom: '10px',
          fontSize: '12px',
          color: 'var(--dim)',
        }}
      >
        <HardDrive size={14} style={{ color: 'var(--accent)' }} />
        <span style={{ fontWeight: 700, color: 'var(--ink)' }}>Папки</span>
        <span>— «+» разворачивает дерево, клик по папке ставит в очередь и плейлист</span>
      </div>

      {rootsLoading && (
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
          <div style={{ fontSize: '13px' }}>Загрузка дерева...</div>
        </div>
      )}

      {!rootsLoading && error && (
        <div style={{ padding: '24px', color: 'var(--danger, #f66)', fontSize: '13px' }}>{error}</div>
      )}

      {!rootsLoading && !error && roots.length === 0 && (
        <div style={{ padding: '40px 16px', textAlign: 'center', color: 'var(--dim)', fontSize: '13px' }}>
          <Folder size={32} style={{ opacity: 0.35, marginBottom: '8px' }} />
          <div>Корневых папок нет</div>
        </div>
      )}

      {!rootsLoading && !error && roots.length > 0 && (
        <div className="tmusic-tree">
          {roots.map((node) => (
            <TreeRow
              key={`f-${node.folder.id}`}
              node={node}
              depth={0}
              expandedIds={expandedIds}
              childrenMap={childrenMap}
              loadingIds={loadingIds}
              onToggle={onToggle}
              onPlayFolder={onPlayFolder}
              onPlayTrack={onPlayTrack}
              onPlayNext={onPlayNext}
              onEnqueue={onEnqueue}
              playingId={playingId}
              currentTrack={currentTrack}
              isPlaying={isPlaying}
              onTogglePlayCurrent={onTogglePlayCurrent}
            />
          ))}
        </div>
      )}
    </div>
  )
}
