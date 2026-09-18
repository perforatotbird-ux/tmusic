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
  Plus,
  Minus,
  HardDrive,
} from 'lucide-react'
import { vfsApi } from '../api/vfs'
import { playlistsApi } from '../api/playlists'
import { usePlayerStore } from '../store/playerStore'
import { useAuthStore } from '../store/authStore'

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

function TreeRow({ node, depth, expandedIds, childrenMap, loadingIds, onToggle, onPlayFolder, onPlayTrack, playingId }) {
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
          cursor: isFolder ? 'pointer' : 'default',
          paddingLeft: `calc(6px + ${depth} * var(--tree-indent, 18px))`,
          background: playingId && isFolder && playingId === f.id ? 'var(--accent-muted)' : 'transparent',
          minHeight: 44,
        }}
        onClick={isFolder ? () => onPlayFolder(f) : undefined}
        title={isFolder ? 'Клик — в плейлист и воспроизведение' : undefined}
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
          <span style={{ width: 22, minWidth: 22, display: 'inline-flex', justifyContent: 'center', color: 'var(--dim)' }}>
            <Music size={14} />
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

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontWeight: isFolder ? 650 : 500,
              fontSize: 13.5,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              color: 'var(--ink)',
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

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (isFolder) onPlayFolder(f)
            else
              onPlayTrack(t)
          }}
          title={isFolder ? 'В плейлист + играть' : 'Играть трек'}
          style={{
            width: 36,
            height: 36,
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
          {isFolder && playingId === f.id ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Play size={12} fill="#141210" />
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
              playingId={playingId}
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
  const { playQueue } = usePlayerStore()
  const { isAuthenticated } = useAuthStore()
  const reqSeq = useRef(0)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setRootsLoading(true)
      setError(null)
      try {
        const list = await vfsApi.roots()
        if (!cancelled) setRoots((list || []).map((f) => ({ type: 'folder', folder: f })))
      } catch (e) {
        console.error(e)
        if (!cancelled) {
          setError('Не удалось загрузить папки')
          setRoots([])
        }
      } finally {
        if (!cancelled) setRootsLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

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
        if (my === reqSeq.current || true) {
          setChildrenMap((prev) => ({ ...prev, [nodeId]: kids }))
        }
      } catch (e) {
        console.error(e)
        setChildrenMap((prev) => ({ ...prev, [nodeId]: [] }))
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
          const lists = await playlistsApi.list()
          const existing = (lists || []).find(
            (p) => (p.name || '').toLowerCase() === (folder.name || '').toLowerCase()
          )
          const ids = tracks.slice(0, 500).map((t) => t.id)
          const target = existing || (await playlistsApi.create(folder.name))
          const detail = await playlistsApi.get(target.id)
          for (const row of detail?.tracks || []) {
            try {
              await playlistsApi.removeItem(target.id, row.id)
            } catch {
              /* ignore */
            }
          }
          for (const fileId of ids) {
            try {
              await playlistsApi.addTrack(target.id, fileId)
            } catch {
              /* ignore */
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
          <div style={{ fontSize: '13px' }}>Загрузка дерева…</div>
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
              playingId={playingId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
