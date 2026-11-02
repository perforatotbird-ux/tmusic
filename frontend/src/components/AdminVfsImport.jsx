import { startSerialPolling } from '../utils/serialPolling'
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  File,
  FileText,
  Folder,
  FolderOpen,
  FolderTree,
  Loader2,
  Music,
  Play,
  RefreshCw,
  Search,
  Square,
  UploadCloud,
  X,
} from 'lucide-react'
import { adminApi } from '../api/admin'
import { useConfirm } from './ui/useConfirm'

function formatBytes(value) {
  if (!value && value !== 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = Number(value)
  if (!Number.isFinite(size)) return '—'
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
}

function formatNumber(value) {
  const n = Number(value || 0)
  return (Number.isFinite(n) ? n : 0).toLocaleString('ru-RU')
}

export function AdminVfsImport({ targetFolders = [], onImported, disabled = false }) {
  const [file, setFile] = useState(null)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [staged, setStaged] = useState(null)

  // Tree state
  const [expanded, setExpanded] = useState(() => new Set())
  const [childrenMap, setChildrenMap] = useState({})
  const [loadingNodes, setLoadingNodes] = useState(() => new Set())
  const [selectedFolders, setSelectedFolders] = useState(() => new Set())
  const [selectedFiles, setSelectedFiles] = useState(() => new Set())
  const [treeSearch, setTreeSearch] = useState('')

  // Target and import state
  const [targetRootId, setTargetRootId] = useState('')
  // Live-DB receiver tree (симметрия с деревом бэкапа слева)
  const [targetRoots, setTargetRoots] = useState([])
  const [targetRootFiles, setTargetRootFiles] = useState([])
  const [targetExpanded, setTargetExpanded] = useState(() => new Set())
  const [targetChildrenMap, setTargetChildrenMap] = useState({})
  const [targetLoading, setTargetLoading] = useState(() => new Set())
  const [targetSearch, setTargetSearch] = useState('')
  const [targetTreeError, setTargetTreeError] = useState(null)
  const [preserveParentPath, setPreserveParentPath] = useState(true)
  const [catalogKind, setCatalogKind] = useState('compilation')
  const [diffBusy, setDiffBusy] = useState(false)
  const [preview, setPreview] = useState(null)
  const [importBusy, setImportBusy] = useState(false)
  const [progress, setProgress] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const { confirm, confirmDialog } = useConfirm()

  const pollRef = useRef(null)
  const importEpoch = useRef(0)
  const startingRef = useRef(false)
  const stopPolling = useCallback(() => {
    importEpoch.current += 1
    pollRef.current?.stop()
    pollRef.current = null
  }, [])
  useEffect(() => () => stopPolling(), [stopPolling])

  // Загрузка корней live-БД приёмника
  const loadTargetRoots = useCallback(async () => {
    setTargetTreeError(null)
    try {
      const resp = await adminApi.getLiveVfsChildren(null)
      setTargetRoots(resp.folders || [])
      setTargetRootFiles(resp.files || [])
    } catch (e) {
      setTargetTreeError(e.message || 'Ошибка загрузки дерева T-Music')
    }
  }, [])

  useEffect(() => {
    if (staged?.import_id) loadTargetRoots()
  }, [staged?.import_id, loadTargetRoots])

  const toggleTargetExpand = async (folderId) => {
    setTargetExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(folderId)) next.delete(folderId)
      else next.add(folderId)
      return next
    })
    if (!targetChildrenMap[folderId]) {
      setTargetLoading((prev) => new Set(prev).add(folderId))
      try {
        const resp = await adminApi.getLiveVfsChildren(folderId)
        setTargetChildrenMap((prev) => ({
          ...prev,
          [folderId]: { folders: resp.folders || [], files: resp.files || [] },
        }))
      } catch (e) {
        setTargetTreeError(e.message || 'Ошибка загрузки подпапок T-Music')
      } finally {
        setTargetLoading((prev) => {
          const next = new Set(prev)
          next.delete(folderId)
          return next
        })
      }
    }
  }

  const resetAll = () => {
    stopPolling()
    setFile(null)
    setStaged(null)
    setExpanded(new Set())
    setChildrenMap({})
    setLoadingNodes(new Set())
    setSelectedFolders(new Set())
    setSelectedFiles(new Set())
    setTreeSearch('')
    setTargetRoots([])
    setTargetRootFiles([])
    setTargetExpanded(new Set())
    setTargetChildrenMap({})
    setTargetLoading(new Set())
    setTargetSearch('')
    setTargetTreeError(null)
    setTargetRootId('')
    setPreserveParentPath(true)
    setCatalogKind('compilation')
    setPreview(null)
    setProgress(null)
    setResult(null)
    setError(null)
    setDiffBusy(false)
    setImportBusy(false)
  }

  const chooseFile = (e) => {
    const selected = e.target.files?.[0] || null
    setFile(selected)
    setError(null)
  }

  // Upload backup and get root folders
  const handleUpload = async () => {
    if (!file) return
    setUploadBusy(true)
    setError(null)
    setResult(null)
    setPreview(null)
    try {
      const resp = await adminApi.uploadVfsBackup(file)
      setStaged(resp)
      setSelectedFolders(new Set())
      setSelectedFiles(new Set())
    } catch (e) {
      setError(e.message || 'Ошибка загрузки и разбора бэкапа')
    } finally {
      setUploadBusy(false)
    }
  }

  // Lazy load children of a folder
  const toggleExpand = async (folderId) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(folderId)) {
        next.delete(folderId)
      } else {
        next.add(folderId)
      }
      return next
    })

    if (!childrenMap[folderId] && staged?.import_id) {
      setLoadingNodes((prev) => new Set(prev).add(folderId))
      try {
        const resp = await adminApi.getVfsImportChildren(staged.import_id, folderId)
        setChildrenMap((prev) => ({
          ...prev,
          [folderId]: {
            folders: resp.folders || [],
            files: resp.files || [],
          },
        }))
      } catch (e) {
        setError(e.message || 'Ошибка загрузки подпапок')
      } finally {
        setLoadingNodes((prev) => {
          const next = new Set(prev)
          next.delete(folderId)
          return next
        })
      }
    }
  }

  // childId -> parentId index, rebuilt only when childrenMap changes (O(n)),
  // so findAncestorIds no longer rescans every parent on each step (was O(n²)).
  const parentByChild = useMemo(() => {
    const map = new Map()
    for (const [pid, data] of Object.entries(childrenMap)) {
      for (const f of (data.folders || [])) map.set(f.id, Number(pid))
    }
    return map
  }, [childrenMap])

  const findAncestorIds = (folderId) => {
    const ancestors = []
    const seen = new Set()
    let currId = folderId
    while (currId && !seen.has(currId)) {
      seen.add(currId)
      const parentId = parentByChild.get(currId)
      if (!parentId) break
      ancestors.push(parentId)
      currId = parentId
    }
    return ancestors
  }

  // Recursive selection toggle for folders
  const toggleFolderSelection = (folder, checked) => {
    const collectDescendantIds = (fid) => {
      const folderIds = [fid]
      const fileIds = []
      const childData = childrenMap[fid]
      if (childData) {
        for (const sub of childData.folders) {
          const res = collectDescendantIds(sub.id)
          folderIds.push(...res.folderIds)
          fileIds.push(...res.fileIds)
        }
        for (const vf of childData.files) {
          fileIds.push(vf.id)
        }
      }
      return { folderIds, fileIds }
    }

    const { folderIds, fileIds } = collectDescendantIds(folder.id)
    const ancestorIds = findAncestorIds(folder.id)

    setSelectedFolders((prev) => {
      const next = new Set(prev)
      if (checked) {
        folderIds.forEach((id) => next.add(id))
      } else {
        folderIds.forEach((id) => next.delete(id))
        ancestorIds.forEach((id) => next.delete(id))
      }
      return next
    })

    setSelectedFiles((prev) => {
      const next = new Set(prev)
      if (checked) {
        fileIds.forEach((id) => next.add(id))
      } else {
        fileIds.forEach((id) => next.delete(id))
      }
      return next
    })
  }

  const toggleFileSelection = (fileId, checked) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev)
      if (checked) next.add(fileId)
      else next.delete(fileId)
      return next
    })
  }

  const selectAll = () => {
    const allFids = new Set()
    const allVids = new Set()
    ;(staged?.root_folders || []).forEach((f) => allFids.add(f.id))
    ;(staged?.root_files || []).forEach((f) => allVids.add(f.id))
    Object.values(childrenMap).forEach((c) => {
      ;(c.folders || []).forEach((f) => allFids.add(f.id))
      ;(c.files || []).forEach((f) => allVids.add(f.id))
    })
    setSelectedFolders(allFids)
    setSelectedFiles(allVids)
  }

  const deselectAll = () => {
    setSelectedFolders(new Set())
    setSelectedFiles(new Set())
  }

  // Calculate preview diff
  const calculatePreview = useCallback(async () => {
    if (!staged?.import_id || (selectedFolders.size === 0 && selectedFiles.size === 0)) {
      setPreview(null)
      return
    }
    setDiffBusy(true)
    setError(null)
    try {
      const resp = await adminApi.previewTreeImport(staged.import_id, {
        selected_folder_ids: Array.from(selectedFolders),
        selected_file_ids: Array.from(selectedFiles),
        target_root_id: targetRootId ? Number(targetRootId) : null,
        preserve_parent_path: preserveParentPath,
        catalog_kind: catalogKind,
      })
      setPreview(resp)
    } catch (e) {
      setError(e.message || 'Ошибка расчёта изменений')
    } finally {
      setDiffBusy(false)
    }
  }, [staged, selectedFolders, selectedFiles, targetRootId, preserveParentPath, catalogKind])

  // Trigger preview when selection or target changes
  useEffect(() => {
    const timer = setTimeout(() => {
      if (staged?.import_id && (selectedFolders.size > 0 || selectedFiles.size > 0)) {
        calculatePreview()
      } else {
        setPreview(null)
      }
    }, 350)
    return () => clearTimeout(timer)
  }, [selectedFolders, selectedFiles, targetRootId, preserveParentPath, catalogKind, staged?.import_id, calculatePreview])

  // Run import
  const handleRunImport = async () => {
    if (!staged?.import_id || pollRef.current || startingRef.current) return
    startingRef.current = true
    let polling = null
    const summary = preview?.summary || {}
    try {
      const confirmed = await confirm({
        title: 'Запустить импорт',
        message:
          `Импортировать ${formatNumber(summary.files_imported || 0)} новых треков в T-Music?\n\n` +
          `Папок будет создано: ${summary.folders_create || 0}\n` +
          `Папок будет объединено: ${summary.folders_reuse || 0}\n` +
          `Существующие дубликаты (${summary.files_skipped || 0} файлов) будут пропущены.`,
        confirmLabel: 'Импортировать',
      })
      if (!confirmed) return

      if (importBusy) return
      stopPolling()
      const generation = importEpoch.current
      const importId = staged.import_id
      setImportBusy(true)
      setError(null)
      setProgress({ current: 0, total: summary.files_total || 0, message: 'Запуск импорта…' })

      polling = startSerialPolling(
        () => adminApi.getVfsImportStatus(importId),
        (status) => {
          if (generation === importEpoch.current && status?.progress) setProgress(status.progress)
        },
      )
      pollRef.current = polling
    } finally {
      startingRef.current = false
    }

    if (!polling) return
    const generation = importEpoch.current
    const importId = staged.import_id

    try {
      const resp = await adminApi.runTreeImport(importId, {
        selected_folder_ids: Array.from(selectedFolders),
        selected_file_ids: Array.from(selectedFiles),
        target_root_id: targetRootId ? Number(targetRootId) : null,
        confirm: true,
        preserve_parent_path: preserveParentPath,
        catalog_kind: catalogKind,
      })
      polling.stop()
      if (generation !== importEpoch.current) return
      setResult(resp)
      setPreview(null)
      window.dispatchEvent(new CustomEvent('tmusic:vfs-imported', { detail: resp }))
      await onImported?.(resp)
    } catch (e) {
      polling.stop()
      if (generation !== importEpoch.current) return
      setError(e.message || 'Ошибка импорта')
    } finally {
      polling.stop()
      if (pollRef.current === polling) pollRef.current = null
      if (generation === importEpoch.current) setImportBusy(false)
    }
  }

  // Recursive Tree Node component
  const renderTreeNode = (folder, depth = 0) => {
    const isExpanded = expanded.has(folder.id)
    const isSelected = selectedFolders.has(folder.id)
    const isLoading = loadingNodes.has(folder.id)
    const children = childrenMap[folder.id]
    const matchesFilter =
      !treeSearch ||
      folder.name.toLowerCase().includes(treeSearch.toLowerCase()) ||
      (children?.folders || []).some((f) => f.name.toLowerCase().includes(treeSearch.toLowerCase())) ||
      (children?.files || []).some((f) => f.name.toLowerCase().includes(treeSearch.toLowerCase()))

    if (!matchesFilter && depth === 0) return null

    return (
      <div key={`folder-${folder.id}`} style={{ userSelect: 'none' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 8px',
            paddingLeft: depth * 18 + 8,
            borderRadius: 6,
            background: isSelected ? 'rgba(255, 178, 62, 0.08)' : 'transparent',
            borderBottom: '1px solid rgba(255, 255, 255, 0.03)',
            transition: 'background 0.15s ease',
          }}
        >
          {/* Expand chevron */}
          <button
            type="button"
            onClick={() => toggleExpand(folder.id)}
            style={{
              background: 'transparent',
              border: 0,
              padding: 2,
              cursor: 'pointer',
              color: 'var(--dim)',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            {isLoading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : isExpanded ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )}
          </button>

          {/* Checkbox */}
          <button
            type="button"
            onClick={() => toggleFolderSelection(folder, !isSelected)}
            style={{
              background: 'transparent',
              border: 0,
              padding: 0,
              cursor: 'pointer',
              color: isSelected ? 'var(--accent)' : 'var(--dim)',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            {isSelected ? <CheckSquare size={16} /> : <Square size={16} />}
          </button>

          {/* Folder icon */}
          {isExpanded ? (
            <FolderOpen size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          ) : (
            <Folder size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
          )}

          {/* Folder name */}
          <span
            onClick={() => toggleExpand(folder.id)}
            style={{
              flex: 1,
              fontSize: 12.5,
              fontWeight: 600,
              cursor: 'pointer',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={folder.name}
          >
            {folder.name}
          </span>

          {/* Badge */}
          <span style={{ fontSize: 11, color: 'var(--dim)', flexShrink: 0 }}>
            {folder.subfolder_count > 0 && `${folder.subfolder_count} пап.`}
            {folder.subfolder_count > 0 && folder.file_count > 0 && ' · '}
            {folder.file_count > 0 && `${folder.file_count} трек.`}
          </span>
        </div>

        {/* Children (subfolders and files) */}
        {isExpanded && children && (
          <div>
            {(children.folders || []).map((sub) => renderTreeNode(sub, depth + 1))}
            {(children.files || []).map((vf) => {
              const fileSelected = selectedFiles.has(vf.id)
              const matchesFileFilter = !treeSearch || vf.name.toLowerCase().includes(treeSearch.toLowerCase())
              if (!matchesFileFilter) return null

              return (
                <div
                  key={`file-${vf.id}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 8px',
                    paddingLeft: (depth + 1) * 18 + 18,
                    borderRadius: 6,
                    background: fileSelected ? 'rgba(255, 178, 62, 0.05)' : 'transparent',
                    borderBottom: '1px solid rgba(255, 255, 255, 0.02)',
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleFileSelection(vf.id, !fileSelected)}
                    style={{
                      background: 'transparent',
                      border: 0,
                      padding: 0,
                      cursor: 'pointer',
                      color: fileSelected ? 'var(--accent)' : 'var(--dim)',
                      display: 'flex',
                      alignItems: 'center',
                    }}
                  >
                    {fileSelected ? <CheckSquare size={15} /> : <Square size={15} />}
                  </button>
                  {vf.is_audio ? (
                    <Music size={14} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  ) : (
                    <File size={14} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                  )}
                  <span
                    style={{
                      flex: 1,
                      fontSize: 12,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      color: 'var(--ink)',
                    }}
                    title={vf.name}
                  >
                    {vf.name}
                  </span>
                  <span style={{ fontSize: 10.5, color: 'var(--dim)', flexShrink: 0 }}>
                    {formatBytes(vf.size)}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  // Узел дерева приёмника (live-БД): иконка папки + Expand + одиночный выбор цели
  const renderTargetNode = (folder, depth = 0) => {
    const isExpanded = targetExpanded.has(folder.id)
    const isSelected = String(targetRootId) === String(folder.id)
    const isLoading = targetLoading.has(folder.id)
    const children = targetChildrenMap[folder.id]
    const matchesFilter =
      !targetSearch ||
      folder.name.toLowerCase().includes(targetSearch.toLowerCase()) ||
      (children?.folders || []).some((f) => f.name.toLowerCase().includes(targetSearch.toLowerCase()))
    if (!matchesFilter && depth === 0) return null
    return (
      <div key={`target-folder-${folder.id}`} style={{ userSelect: 'none' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 8px',
            paddingLeft: depth * 18 + 8,
            borderRadius: 6,
            background: isSelected ? 'rgba(80, 200, 120, 0.12)' : 'transparent',
            borderBottom: '1px solid rgba(255, 255, 255, 0.03)',
          }}
        >
          <button
            type="button"
            onClick={() => toggleTargetExpand(folder.id)}
            style={{
              background: 'transparent', border: 0, padding: 2, cursor: 'pointer',
              color: 'var(--dim)', display: 'flex', alignItems: 'center',
            }}
          >
            {isLoading ? <Loader2 size={14} className="animate-spin" />
              : isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
          {isExpanded
            ? <FolderOpen size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
            : <Folder size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
          <span
            onClick={() => setTargetRootId(String(folder.id))}
            title={`Выбрать как приёмник: ${folder.name}`}
            style={{
              flex: 1, fontSize: 12.5, fontWeight: isSelected ? 800 : 600, cursor: 'pointer',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              color: isSelected ? 'var(--accent)' : 'var(--ink)',
            }}
          >
            {folder.name}
          </span>
          <span style={{ fontSize: 11, color: 'var(--dim)', flexShrink: 0 }}>
            {folder.subfolder_count > 0 && `${folder.subfolder_count} пап.`}
            {folder.subfolder_count > 0 && folder.file_count > 0 && ' · '}
            {folder.file_count > 0 && `${folder.file_count} трек.`}
          </span>
        </div>
        {isExpanded && children && (
          <div>
            {(children.folders || []).map((sub) => renderTargetNode(sub, depth + 1))}
            {(children.files || []).slice(0, 30).map((vf) => (
              <div
                key={`target-file-${vf.id}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '3px 8px', paddingLeft: (depth + 1) * 18 + 18,
                }}
              >
                {vf.is_audio
                  ? <Music size={13} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                  : <File size={13} style={{ color: 'var(--dim)', flexShrink: 0 }} />}
                <span style={{ flex: 1, fontSize: 11.5, color: 'var(--dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={vf.name}>
                  {vf.name}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  const selectedTargetName = useMemo(() => {
    if (!targetRootId) return 'Корень T-Music'
    const all = [
      ...targetRoots,
      ...Object.values(targetChildrenMap).flatMap((c) => c.folders || []),
      ...targetFolders,
    ]
    const found = all.find((f) => String(f.id) === String(targetRootId))
    return found ? `${found.name} · #${found.id}` : `#${targetRootId}`
  }, [targetRootId, targetRoots, targetChildrenMap, targetFolders])

  const inputStyle = {
    background: 'var(--bg)',
    border: '1px solid var(--line)',
    color: 'var(--ink)',
    borderRadius: 8,
    padding: '8px 10px',
    minHeight: 38,
    fontSize: 13,
  }

  return (
    <div
      style={{
        marginTop: 14,
        paddingTop: 14,
        borderTop: '1px solid var(--line)',
        display: 'grid',
        gap: 12,
      }}
    >
      {confirmDialog}
      {/* Title */}
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ fontWeight: 800, fontSize: 13 }}>Импорт музыки из бэкапа Cloudbyte (SQL / ZIP)</div>
          <div style={{ color: 'var(--dim)', fontSize: 11, marginTop: 3 }}>
            Построение VFS бэкапа с lazy-загрузкой подпапок и умным объединением с существующими папками T-Music.
          </div>
        </div>
        <FolderTree size={20} style={{ color: 'var(--accent)' }} />
      </div>

      {/* Step 1: Upload Backup */}
      {!staged ? (
        <div
          style={{
            padding: 12,
            border: '1px solid var(--line)',
            borderRadius: 9,
            background: 'rgba(255,255,255,0.02)',
            display: 'grid',
            gap: 10,
          }}
        >
          <label style={{ display: 'grid', gap: 4, fontSize: 12 }}>
            <span style={{ color: 'var(--dim)' }}>Файл бэкапа (.sql дамп или .zip архив)</span>
            <input
              type="file"
              accept=".sql,.zip,.dump,text/plain,application/zip,application/x-zip-compressed"
              onChange={chooseFile}
              disabled={disabled || uploadBusy}
              style={{ ...inputStyle, padding: '6px 8px' }}
            />
          </label>

          {file && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--ink)', fontSize: 12 }}>
              <FileText size={15} style={{ color: 'var(--accent)' }} />
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {file.name}
              </span>
              <span style={{ color: 'var(--dim)' }}>{formatBytes(file.size)}</span>
              {!uploadBusy && (
                <button
                  type="button"
                  onClick={() => setFile(null)}
                  aria-label="Убрать выбранный файл"
                  title="Убрать файл"
                  style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--ink)', display: 'inline-flex' }}
                >
                  <X size={15} />
                </button>
              )}
            </div>
          )}

          <div>
            <button
              type="button"
              onClick={handleUpload}
              disabled={disabled || uploadBusy || !file}
              style={{
                background: 'var(--accent)',
                color: 'var(--on-accent)',
                border: 0,
                borderRadius: 8,
                minHeight: 38,
                padding: '8px 14px',
                fontWeight: 800,
                fontSize: 12.5,
                cursor: disabled || uploadBusy || !file ? 'default' : 'pointer',
                opacity: disabled || uploadBusy || !file ? 0.5 : 1,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 7,
              }}
            >
              {uploadBusy ? <Loader2 size={14} className="animate-spin" /> : <UploadCloud size={14} />}
              {uploadBusy ? 'Чтение структуры бэкапа…' : 'Получить предпросмотр'}
            </button>
          </div>
        </div>
      ) : (
        /* Step 2: Staged Backup View (Two-Panel Layout) */
        <div style={{ display: 'grid', gap: 12 }}>
          {/* File info bar */}
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid var(--line)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 10,
              flexWrap: 'wrap',
              fontSize: 12,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <FileText size={16} style={{ color: 'var(--accent)' }} />
              <span style={{ fontWeight: 700 }}>{staged.filename}</span>
              <span style={{ color: 'var(--dim)' }}>({formatBytes(staged.size_bytes)})</span>
              <span style={{ color: 'var(--dim)' }}>·</span>
              <span style={{ color: 'var(--dim)' }}>Папок: {formatNumber(staged.total_folders)}</span>
              <span style={{ color: 'var(--dim)' }}>·</span>
              <span style={{ color: 'var(--dim)' }}>Файлов: {formatNumber(staged.total_files)}</span>
            </div>
            <button
              type="button"
              onClick={resetAll}
              disabled={importBusy}
              style={{
                background: 'transparent',
                border: '1px solid var(--line)',
                color: 'var(--dim)',
                borderRadius: 6,
                padding: '4px 8px',
                fontSize: 11,
                cursor: 'pointer',
              }}
            >
              Загрузить другой бэкап
            </button>
          </div>

          {/* Two-Column Grid */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(280px, 1.2fr) minmax(280px, 1fr)',
              gap: 12,
              alignItems: 'start',
            }}
          >
            {/* Left Column: Backup VFS Tree */}
            <div
              style={{
                border: '1px solid var(--line)',
                borderRadius: 9,
                background: 'rgba(0,0,0,0.15)',
                display: 'flex',
                flexDirection: 'column',
                height: 480,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  padding: '8px 10px',
                  borderBottom: '1px solid var(--line)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  background: 'rgba(255,255,255,0.02)',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: 12 }}>Дерево бэкапа</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    type="button"
                    onClick={selectAll}
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--accent)',
                      fontSize: 11,
                      cursor: 'pointer',
                      padding: 2,
                    }}
                  >
                    Выбрать все
                  </button>
                  <span style={{ color: 'var(--line)' }}>|</span>
                  <button
                    type="button"
                    onClick={deselectAll}
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--dim)',
                      fontSize: 11,
                      cursor: 'pointer',
                      padding: 2,
                    }}
                  >
                    Снять
                  </button>
                </div>
              </div>

              {/* Tree search input */}
              <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>
                <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                  <Search size={13} style={{ position: 'absolute', left: 8, color: 'var(--dim)' }} />
                  <input
                    value={treeSearch}
                    onChange={(e) => setTreeSearch(e.target.value)}
                    placeholder="Фильтр в дереве…"
                    style={{
                      ...inputStyle,
                      minHeight: 30,
                      padding: '4px 8px 4px 26px',
                      fontSize: 11.5,
                      width: '100%',
                    }}
                  />
                  {treeSearch && (
                    <X
                      size={13}
                      style={{ position: 'absolute', right: 8, color: 'var(--dim)', cursor: 'pointer' }}
                      onClick={() => setTreeSearch('')}
                    />
                  )}
                </div>
              </div>

              {/* Tree scrollable list */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '6px 4px' }}>
                {(staged.root_folders || []).map((folder) => renderTreeNode(folder, 0))}

                {/* Root level files */}
                {(staged.root_files || []).map((vf) => {
                  const fileSelected = selectedFiles.has(vf.id)
                  const matchesFileFilter = !treeSearch || vf.name.toLowerCase().includes(treeSearch.toLowerCase())
                  if (!matchesFileFilter) return null

                  return (
                    <div
                      key={`root-file-${vf.id}`}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 8px',
                        paddingLeft: 16,
                        borderRadius: 6,
                        background: fileSelected ? 'rgba(255, 178, 62, 0.05)' : 'transparent',
                        borderBottom: '1px solid rgba(255, 255, 255, 0.02)',
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => toggleFileSelection(vf.id, !fileSelected)}
                        style={{
                          background: 'transparent',
                          border: 0,
                          padding: 0,
                          cursor: 'pointer',
                          color: fileSelected ? 'var(--accent)' : 'var(--dim)',
                          display: 'flex',
                          alignItems: 'center',
                        }}
                      >
                        {fileSelected ? <CheckSquare size={15} /> : <Square size={15} />}
                      </button>
                      {vf.is_audio ? (
                        <Music size={14} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                      ) : (
                        <File size={14} style={{ color: 'var(--dim)', flexShrink: 0 }} />
                      )}
                      <span
                        style={{
                          flex: 1,
                          fontSize: 12,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          color: 'var(--ink)',
                        }}
                        title={vf.name}
                      >
                        {vf.name}
                      </span>
                      <span style={{ fontSize: 10.5, color: 'var(--dim)', flexShrink: 0 }}>
                        {formatBytes(vf.size)}
                      </span>
                    </div>
                  )
                })}

                {staged.root_folders?.length === 0 && staged.root_files?.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '24px 10px', color: 'var(--dim)', fontSize: 12 }}>
                    Корневые папки не найдены в бэкапе
                  </div>
                )}
              </div>
            </div>

            {/* Right Column: Destination, Diff & Import */}
            <div style={{ display: 'grid', gap: 10 }}>
              {/* Destination selector: live-дерево приёмника текущей БД */}
              <div
                style={{
                  padding: 10,
                  border: '1px solid var(--line)',
                  borderRadius: 9,
                  background: 'rgba(255,255,255,0.02)',
                  display: 'grid',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 700, fontSize: 12 }}>Приёмник в T-Music (текущая база)</span>
                  <button
                    type="button"
                    onClick={loadTargetRoots}
                    style={{ background: 'transparent', border: 0, color: 'var(--accent)', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}
                  >
                    <RefreshCw size={11} /> Обновить
                  </button>
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--dim)' }}>
                  Приёмник: <b style={{ color: 'var(--accent)' }}>{selectedTargetName}</b>
                  {' '}· содержимое папки источника сравнивается с содержимым приёмника, импортируется только отсутствующее.
                </div>
                <div
                  style={{
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    background: 'rgba(0,0,0,0.15)',
                    display: 'flex',
                    flexDirection: 'column',
                    height: 260,
                    overflow: 'hidden',
                  }}
                >
                  <div style={{ padding: '6px 8px', borderBottom: '1px solid var(--line)' }}>
                    <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
                      <Search size={13} style={{ position: 'absolute', left: 8, color: 'var(--dim)' }} />
                      <input
                        value={targetSearch}
                        onChange={(e) => setTargetSearch(e.target.value)}
                        placeholder="Фильтр приёмника…"
                        style={{ ...inputStyle, minHeight: 30, padding: '4px 8px 4px 26px', fontSize: 11.5, width: '100%' }}
                      />
                      {targetSearch && (
                        <button
                          type="button"
                          onClick={() => setTargetSearch('')}
                          aria-label="Очистить фильтр приёмника"
                          title="Очистить"
                          style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--dim)', display: 'inline-flex' }}
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div style={{ flex: 1, overflowY: 'auto', padding: '6px 4px' }}>
                    <div
                      onClick={() => setTargetRootId('')}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px',
                        borderRadius: 6, cursor: 'pointer',
                        background: !targetRootId ? 'rgba(80, 200, 120, 0.12)' : 'transparent',
                        fontSize: 12.5, fontWeight: 700,
                      }}
                    >
                      <Folder size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                      <span style={{ flex: 1 }}>Корень T-Music</span>
                      {!targetRootId && <span style={{ fontSize: 11, color: 'var(--accent)' }}>● приёмник</span>}
                    </div>
                    {targetRoots.map((folder) => renderTargetNode(folder, 0))}
                    {targetTreeError && (
                      <div style={{ fontSize: 11.5, color: 'var(--danger)', padding: 8 }}>{targetTreeError}</div>
                    )}
                    {!targetTreeError && targetRoots.length === 0 && (
                      <div style={{ fontSize: 11.5, color: 'var(--dim)', padding: 12, textAlign: 'center' }}>
                        Корневых папок нет — приёмником будет корень
                      </div>
                    )}
                  </div>
                </div>

                {/* Catalog Kind Selector */}
                <label style={{ display: 'grid', gap: 4, fontSize: 12 }}>
                  <span style={{ fontWeight: 700 }}>Карточка в медиатеке (каталоге)</span>
                  <select
                    value={catalogKind}
                    onChange={(e) => setCatalogKind(e.target.value)}
                    disabled={disabled || importBusy}
                    style={inputStyle}
                  >
                    <option value="compilation">Сборник (compilation) — например: Пранки, Саундтреки</option>
                    <option value="artist">Исполнитель (artist) — для альбомов и дискографий</option>
                    <option value="genre_collection">Жанровая подборка (genre_collection)</option>
                    <option value="none">Без карточки в каталоге (только файлы VFS)</option>
                  </select>
                </label>

                {/* Preserve Parent Path Checkbox */}
                <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, cursor: 'pointer', marginTop: 2 }}>
                  <input
                    type="checkbox"
                    checked={preserveParentPath}
                    onChange={(e) => setPreserveParentPath(e.target.checked)}
                    disabled={disabled || importBusy}
                    style={{ accentColor: 'var(--accent)' }}
                  />
                  <span>Сохранять родительскую папку (например, «Пранки» для «314 Кабинет»)</span>
                </label>

                <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.4, marginTop: 2 }}>
                  Пример: источник <b>DriveK\Music</b> → приёмник <b>Music</b>: всё содержимое источника
                  сравнивается с содержимым приёмника по именам (без учёта регистра), совпавшие папки
                  переиспользуются, совпавшие файлы пропускаются — импортируется только отсутствующее.
                </div>
              </div>

              {/* Selection & Diff Stats */}
              <div
                style={{
                  padding: 10,
                  border: '1px solid var(--line)',
                  borderRadius: 9,
                  background: 'rgba(255,255,255,0.02)',
                  display: 'grid',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ fontWeight: 700, fontSize: 12 }}>Предпросмотр изменений (Diff)</div>
                  <button
                    type="button"
                    onClick={calculatePreview}
                    disabled={diffBusy || importBusy}
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--accent)',
                      fontSize: 11,
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                    }}
                  >
                    <RefreshCw size={11} className={diffBusy ? 'animate-spin' : ''} />
                    Обновить
                  </button>
                </div>

                <div style={{ fontSize: 11.5, color: 'var(--dim)' }}>
                  Отмечено: <b>{selectedFolders.size}</b> папок, <b>{selectedFiles.size}</b> файлов
                </div>

                {diffBusy && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--dim)', padding: '6px 0' }}>
                    <Loader2 size={13} className="animate-spin" />
                    <span>Расчёт изменений…</span>
                  </div>
                )}

                {!preview && !diffBusy && selectedFolders.size === 0 && selectedFiles.size === 0 && (
                  <div style={{ fontSize: 11.5, color: 'var(--dim)', padding: '12px 6px', textAlign: 'center', lineHeight: 1.4 }}>
                    Отметьте нужные папки или файлы в дереве слева для предпросмотра и импорта.
                  </div>
                )}

                {preview && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                    <div className="tmusic-profile-stat-card">
                      <div className="stat-val" style={{ color: 'var(--accent)' }}>
                        {formatNumber(preview.summary?.folders_create)}
                      </div>
                      <div className="stat-lbl">Новых папок</div>
                    </div>
                    <div className="tmusic-profile-stat-card">
                      <div className="stat-val">
                        {formatNumber(preview.summary?.folders_reuse)}
                      </div>
                      <div className="stat-lbl">Папок совпало (reuse)</div>
                    </div>
                    <div className="tmusic-profile-stat-card">
                      <div className="stat-val" style={{ color: 'var(--accent)' }}>
                        {formatNumber(preview.summary?.files_imported)}
                      </div>
                      <div className="stat-lbl">Новых треков</div>
                    </div>
                    <div className="tmusic-profile-stat-card">
                      <div className="stat-val" style={{ color: 'var(--dim)' }}>
                        {formatNumber(preview.summary?.files_skipped)}
                      </div>
                      <div className="stat-lbl">Дубликатов (пропуск)</div>
                    </div>
                  </div>
                )}

                {/* Progress bar during import */}
                {importBusy && progress && (
                  <div style={{ marginTop: 6, display: 'grid', gap: 4 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--dim)' }}>
                      <span>{progress.message || 'Импорт…'}</span>
                      <span>
                        {progress.current || 0} / {progress.total || 0}
                      </span>
                    </div>
                    <div
                      role="progressbar"
                      aria-label="Прогресс импорта"
                      aria-valuemin={0}
                      aria-valuemax={progress.total || 0}
                      aria-valuenow={progress.current || 0}
                      aria-valuetext={`${progress.current || 0} из ${progress.total || 0}`}
                      style={{
                        height: 6,
                        borderRadius: 3,
                        background: 'var(--line)',
                        overflow: 'hidden',
                        position: 'relative',
                      }}
                    >
                      <div
                        style={{
                          height: '100%',
                          background: 'var(--accent)',
                          width: `${progress.total ? Math.min(100, Math.round((progress.current / progress.total) * 100)) : 0}%`,
                          transition: 'width 0.2s ease',
                        }}
                      />
                    </div>
                  </div>
                )}

                {/* Run import button */}
                <div style={{ marginTop: 4 }}>
                  <button
                    type="button"
                    onClick={handleRunImport}
                    disabled={
                      disabled ||
                      importBusy ||
                      (selectedFolders.size === 0 && selectedFiles.size === 0) ||
                      (preview && !preview.summary?.can_run)
                    }
                    style={{
                      background: 'var(--accent)',
                      color: 'var(--on-accent)',
                      border: 0,
                      borderRadius: 8,
                      minHeight: 40,
                      width: '100%',
                      padding: '8px 14px',
                      fontWeight: 800,
                      fontSize: 13,
                      cursor:
                        disabled || importBusy || (selectedFolders.size === 0 && selectedFiles.size === 0)
                          ? 'default'
                          : 'pointer',
                      opacity:
                        disabled || importBusy || (selectedFolders.size === 0 && selectedFiles.size === 0)
                          ? 0.5
                          : 1,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 7,
                    }}
                  >
                    {importBusy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                    {importBusy ? 'Выполняется импорт…' : 'Импортировать в T-Music'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Error alert */}
      {error && (
        <div
          role="alert"
          style={{
            padding: 10,
            borderRadius: 8,
            background: 'rgba(255,107,107,0.12)',
            color: 'var(--danger)',
            fontSize: 12,
          }}
        >
          <AlertTriangle size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          {error}
        </div>
      )}

      {/* Success notification */}
      {result && (
        <div
          style={{
            padding: 10,
            borderRadius: 8,
            background: 'var(--accent-muted)',
            color: 'var(--accent)',
            fontSize: 12,
          }}
        >
          <CheckCircle2 size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
          Импорт завершён: папок создано <b>{formatNumber(result.folders_created)}</b>, объединено{' '}
          <b>{formatNumber(result.folders_reused)}</b>, треков добавлено{' '}
          <b>{formatNumber(result.files_imported)}</b>, пропущено дубликатов{' '}
          <b>{formatNumber(result.files_skipped)}</b>.
        </div>
      )}

      <div style={{ color: 'var(--dim)', fontSize: 11, lineHeight: 1.45 }}>
        Бэкап содержит ссылки на Telegram-сообщения, а не аудиобайты. Для воспроизведения импортированных треков
        нужны совместимые Telegram-аккаунты и ключи шифрования T-Music.
      </div>
    </div>
  )
}
