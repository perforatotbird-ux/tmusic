/**
 * T-Music Admin Panel — DB maintenance, backups, settings, users/roles.
 * Access: role=admin or username admin (same password as panel login).
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  Database,
  HardDriveDownload,
  Settings2,
  Users,
  Activity,
  RefreshCw,
  Play,
  Trash2,
  ShieldAlert,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  FolderHeart,
  ScrollText,
  KeyRound,
  UnlockKeyhole,
  FolderOpen,
} from 'lucide-react'
import { adminApi } from '../api/admin'
import { AdminCatalog } from './AdminCatalog'

const TABS = [
  { id: 'overview', label: 'Обзор', icon: Activity },
  { id: 'catalog', label: 'Каталог', icon: FolderHeart },
  { id: 'db', label: 'База данных', icon: Database },
  { id: 'backups', label: 'Бэкапы', icon: HardDriveDownload },
  { id: 'settings', label: 'Настройки', icon: Settings2 },
  { id: 'users', label: 'Пользователи', icon: Users },
  { id: 'audit', label: 'Аудит', icon: ScrollText },
  { id: 'vfs', label: 'VFS', icon: FolderOpen },
]

function fmtBytes(n) {
  if (!n && n !== 0) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = Number(n)
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`
}

function Btn({ children, onClick, variant = 'ghost', disabled, title }) {
  const styles = {
    primary: {
      background: 'var(--accent)',
      color: '#141210',
      border: 'none',
    },
    ghost: {
      background: 'transparent',
      color: 'var(--ink)',
      border: '1px solid var(--line)',
    },
    danger: {
      background: 'rgba(255,107,107,0.12)',
      color: 'var(--danger)',
      border: '1px solid rgba(255,107,107,0.35)',
    },
  }
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      style={{
        ...styles[variant],
        borderRadius: 8,
        padding: '8px 12px',
        minHeight: 40,
        fontWeight: 700,
        fontSize: 12,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        touchAction: 'manipulation',
      }}
    >
      {children}
    </button>
  )
}

function Panel({ title, children, right }) {
  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--line)',
        borderRadius: 12,
        padding: '14px 16px',
        marginBottom: 14,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: 12,
        }}
      >
        <div style={{ fontWeight: 800, fontSize: 13, letterSpacing: '0.03em' }}>{title}</div>
        {right}
      </div>
      {children}
    </div>
  )
}

export function AdminPanel({ onBack, onOpenCoverPicker }) {
  const [tab, setTab] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [overview, setOverview] = useState(null)
  const [dbStats, setDbStats] = useState(null)
  const [backups, setBackups] = useState(null)
  const [settings, setSettings] = useState(null)
  const [users, setUsers] = useState(null)
  const [audit, setAudit] = useState(null)
  const [health, setHealth] = useState(null)
  const [vfsFolders, setVfsFolders] = useState([])
  const [vfsFiles, setVfsFiles] = useState([])
  const [vfsFolderId, setVfsFolderId] = useState('')
  const [vfsSearch, setVfsSearch] = useState('')
  const [vfsSelectedFiles, setVfsSelectedFiles] = useState([])
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' })

  const flash = useCallback((msg, kind = 'ok') => {
    setToast({ msg, kind })
    setTimeout(() => setToast(null), 3500)
  }, [])

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const ov = await adminApi.overview()
      setOverview(ov)
      setSettings(ov.settings)
      const [bk, us, au, hl] = await Promise.all([
        adminApi.listBackups().catch(() => null),
        adminApi.listUsers().catch(() => null),
        adminApi.listAudit().catch(() => null),
        adminApi.systemHealth().catch(() => null),
      ])
      if (bk) setBackups(bk)
      if (us) setUsers(us)
      if (au) setAudit(au)
      if (hl) setHealth(hl)
    } catch (e) {
      setError(e.message || 'Нет доступа к админке')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshVfs = useCallback(async () => {
    setBusy(true)
    try {
      const [folders, files] = await Promise.all([
        adminApi.vfsFolders({ search: vfsSearch.trim(), include_trash: true }),
        adminApi.vfsFiles({ search: vfsSearch.trim(), folder_id: vfsFolderId || undefined, include_trash: true }),
      ])
      setVfsFolders(folders?.items || [])
      setVfsFiles(files?.items || [])
      setVfsSelectedFiles([])
    } catch (e) {
      flash(e.message || 'Ошибка загрузки VFS', 'err')
    } finally {
      setBusy(false)
    }
  }, [flash, vfsFolderId, vfsSearch])

  useEffect(() => {
    if (tab === 'vfs') refreshVfs()
  }, [tab, refreshVfs])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  useEffect(() => {
    if (tab !== 'db') return
    ;(async () => {
      setBusy(true)
      try {
        setDbStats(await adminApi.dbStats())
      } catch (e) {
        flash(e.message || 'Ошибка статистики БД', 'err')
      } finally {
        setBusy(false)
      }
    })()
  }, [tab])

  const runAction = async (fn, okMsg) => {
    setBusy(true)
    try {
      await fn()
      flash(okMsg || 'Готово')
      await loadAll()
    } catch (e) {
      flash(e.message || 'Ошибка', 'err')
    } finally {
      setBusy(false)
    }
  }

  const runVfsAction = async (fn, okMsg) => {
    setBusy(true)
    try {
      await fn()
      await refreshVfs()
      flash(okMsg || 'Готово')
    } catch (e) {
      flash(e.message || 'Ошибка VFS', 'err')
    } finally {
      setBusy(false)
    }
  }

  if (loading && !overview) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: 'var(--dim)' }}>
        <Loader2 size={28} className="animate-spin" style={{ color: 'var(--accent)' }} />
        <div style={{ marginTop: 12, fontSize: 13 }}>Загрузка админки…</div>
      </div>
    )
  }

  if (error && !overview) {
    return (
      <div style={{ padding: 24, color: 'var(--danger)' }}>
        <ShieldAlert size={20} style={{ marginBottom: 8 }} />
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Нет доступа</div>
        <div style={{ fontSize: 13, color: 'var(--dim)', marginBottom: 12 }}>{error}</div>
        {onBack && <Btn onClick={onBack}>Назад</Btn>}
      </div>
    )
  }

  return (
    <div className="tmusic-admin-panel">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          marginBottom: 14,
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 16 }} className="title-zine">
          АДМИНКА
        </div>
        <span style={{ fontSize: 11, color: 'var(--dim)' }}>
          {overview?.viewer?.username} · роль {overview?.viewer?.role}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Btn onClick={loadAll} title="Обновить">
            <RefreshCw size={14} /> Обновить
          </Btn>
          {onBack && <Btn onClick={onBack}>К профилю</Btn>}
        </div>
      </div>

      {toast && (
        <div
          style={{
            marginBottom: 12,
            padding: '10px 12px',
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            background: toast.kind === 'err' ? 'rgba(255,107,107,0.12)' : 'var(--accent-muted)',
            color: toast.kind === 'err' ? 'var(--danger)' : 'var(--accent)',
            border: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          {toast.kind === 'err' ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
          {toast.msg}
        </div>
      )}

      <div className="tmusic-library-bar" style={{ marginBottom: 12 }}>
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              type="button"
              className={`tmusic-library-bar-btn ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              <Icon size={14} /> {t.label}
            </button>
          )
        })}
      </div>

      {tab === 'overview' && overview && (
        <>
          <Panel title="Библиотека">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(110px,1fr))', gap: 8 }}>
              {[
                ['Артисты', overview.library?.artists],
                ['Альбомы', overview.library?.albums],
                ['Треки', overview.library?.tracks],
                ['Плейлисты', overview.library?.playlists],
                ['Пользователи', overview.library?.users],
                ['TG аккаунты', overview.library?.tg_accounts],
              ].map(([l, v]) => (
                <div key={l} className="tmusic-profile-stat-card">
                  <div className="stat-val">{v ?? '—'}</div>
                  <div className="stat-lbl">{l}</div>
                </div>
              ))}
            </div>
          </Panel>
          <Panel
            title="Система"
            right={
              <Btn disabled={busy} onClick={() => runAction(() => adminApi.reconnectTelegram(), 'Telegram-кластер переподключён')}>
                <RefreshCw size={12} /> Переподключить Telegram
              </Btn>
            }
          >
            <div style={{ fontSize: 12.5, color: 'var(--ink)', display: 'grid', gap: 6 }}>
              <div>Окружение: <b>{overview.environment}</b></div>
               <div>БД: {overview.database?.reachable ? overview.database.db_size : 'недоступна'}</div>
               <div>Health endpoint: {health?.database?.reachable ? 'готов' : 'недоступен'}</div>
              <div>
                Подключения: {overview.database?.connections} / {overview.database?.max_connections}
              </div>
              <div>
                Telegram: {overview.telegram?.clients_healthy}/{overview.telegram?.clients_total} онлайн
                {overview.telegram?.primary_id != null && ` · primary #${overview.telegram.primary_id}`}
              </div>
              <div>
                RAM-кэш: {overview.ram_cache?.items ?? 0} трек(ов), {fmtBytes(overview.ram_cache?.bytes)}
              </div>
              <div>
                Последний бэкап:{' '}
                {overview.backups?.latest
                  ? `${overview.backups.latest.name} (${fmtBytes(overview.backups.latest.size_bytes)})`
                  : 'нет'}
              </div>
              <div>
                Enrichment: Last.fm meta {overview.enrichment?.lastfm_metadata ? 'да' : 'нет'}, scrobble{' '}
                {overview.enrichment?.lastfm_scrobble ? 'да' : 'нет'}, Discogs{' '}
                {overview.enrichment?.discogs ? 'да' : 'нет'}
              </div>
            </div>
          </Panel>
        </>
      )}

      {tab === 'catalog' && <AdminCatalog onOpenCoverPicker={onOpenCoverPicker} />}

      {tab === 'db' && (
        <>
          <Panel
            title="Обслуживание БД"
            right={
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Btn disabled={busy} onClick={() => runAction(() => adminApi.vacuum(true), 'VACUUM ANALYZE выполнен')}>
                  <Play size={12} /> VACUUM
                </Btn>
                <Btn disabled={busy} onClick={() => runAction(() => adminApi.analyze(), 'ANALYZE выполнен')}>
                  ANALYZE
                </Btn>
                <Btn disabled={busy} onClick={() => runAction(() => adminApi.reindex(), 'REINDEX выполнен')}>
                  REINDEX
                </Btn>
                <Btn
                  disabled={busy}
                  onClick={() => runAction(() => adminApi.ensureSchema(), 'Схема/индексы обновлены')}
                >
                  Ensure schema
                </Btn>
                <Btn
                  disabled={busy}
                  onClick={() => runAction(() => adminApi.purgeTokens(), 'Просроченные токены удалены')}
                >
                  Purge tokens
                </Btn>
              </div>
            }
          >
            {dbStats?.overview && (
              <div style={{ fontSize: 12.5, display: 'grid', gap: 4, marginBottom: 10 }}>
                <div>
                  Размер: <b>{dbStats.overview.db_size}</b> · таблиц: {dbStats.overview.tables}
                </div>
                <div style={{ color: 'var(--dim)', wordBreak: 'break-all' }}>
                  {dbStats.overview.server_version}
                </div>
                <div style={{ color: 'var(--dim)' }}>Uptime: {dbStats.overview.uptime}</div>
              </div>
            )}
            {dbStats?.tables?.length > 0 && (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--dim)', textAlign: 'left' }}>
                      <th style={{ padding: 6 }}>Таблица</th>
                      <th style={{ padding: 6 }}>Rows</th>
                      <th style={{ padding: 6 }}>Dead</th>
                      <th style={{ padding: 6 }}>Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dbStats.tables.map((t) => (
                      <tr key={t.table} style={{ borderTop: '1px solid var(--line)' }}>
                        <td style={{ padding: 6 }}>{t.table}</td>
                        <td style={{ padding: 6 }}>{t.live_rows}</td>
                        <td style={{ padding: 6 }}>{t.dead_rows}</td>
                        <td style={{ padding: 6 }}>{t.total_size}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </>
      )}

      {tab === 'backups' && (
        <Panel
          title="Бэкапы PostgreSQL (pg_dump)"
          right={
            <Btn
              variant="primary"
              disabled={busy}
              onClick={() => runAction(() => adminApi.runBackup(), 'Бэкап создан')}
            >
              {busy ? <Loader2 size={14} className="animate-spin" /> : <HardDriveDownload size={14} />} Создать сейчас
            </Btn>
          }
        >
          {settings && (
            <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 10 }}>
              Расписание: {settings.backup_enabled ? 'включено' : 'выключено'} · каждые{' '}
              {settings.backup_interval_hours} ч · хранить {settings.backup_retention} · папка{' '}
              <code style={{ color: 'var(--accent)' }}>{settings.backup_dir}</code>
            </div>
          )}
          {!backups?.items?.length && (
            <div style={{ fontSize: 13, color: 'var(--dim)' }}>Файлов бэкапов пока нет</div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {(backups?.items || []).map((b) => (
              <div
                key={b.name}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 6px',
                  borderBottom: '1px solid var(--line)',
                  fontSize: 12.5,
                  flexWrap: 'wrap',
                }}
              >
                <div style={{ flex: 1, minWidth: 140 }}>
                  <div style={{ fontWeight: 700 }}>{b.name}</div>
                  <div style={{ color: 'var(--dim)', fontSize: 11 }}>
                    {fmtBytes(b.size_bytes)} · {b.created_at}
                  </div>
                </div>
                <a
                  href={adminApi.backupDownloadUrl(b.name)}
                  style={{ color: 'var(--accent)', fontSize: 12, fontWeight: 700 }}
                >
                  Скачать
                </a>
                {b.name.endsWith('.dump') && (
                  <>
                    <Btn
                      disabled={busy}
                      onClick={() => runAction(() => adminApi.restoreBackup(b.name, { dry_run: true }), 'Архив проверен')}
                    >
                      Проверить
                    </Btn>
                    <Btn
                      variant="danger"
                      disabled={busy}
                      onClick={() => {
                        const confirmation = window.prompt(`Для восстановления введите имя файла: ${b.name}`)
                        if (confirmation === b.name && window.confirm('Восстановить базу данных из этого бэкапа?')) {
                          runAction(
                            () => adminApi.restoreBackup(b.name, { dry_run: false, confirm: confirmation }),
                            'База восстановлена'
                          )
                        }
                      }}
                    >
                      Восстановить
                    </Btn>
                  </>
                )}
                <Btn
                  variant="danger"
                  disabled={busy}
                  onClick={() => runAction(() => adminApi.deleteBackup(b.name), 'Файл удалён')}
                >
                  <Trash2 size={12} />
                </Btn>
              </div>
            ))}
          </div>
          {backups?.runs?.length > 0 && (
            <div style={{ marginTop: 14, borderTop: '1px solid var(--line)', paddingTop: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 6 }}>История операций</div>
              {(backups.runs || []).map((run) => (
                <div key={run.id} style={{ display: 'flex', gap: 8, fontSize: 11, color: 'var(--dim)', padding: '4px 0' }}>
                  <span>{run.kind}</span>
                  <span style={{ color: run.status === 'failed' ? 'var(--danger)' : 'var(--accent)' }}>{run.status}</span>
                  <span>{run.name || '—'}</span>
                  <span>{run.started_at}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {tab === 'vfs' && (
        <>
          <Panel
            title="VFS: папки и треки"
            right={
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <Btn disabled={busy} onClick={refreshVfs}><RefreshCw size={12} /> Обновить</Btn>
                <Btn disabled={busy} onClick={() => runVfsAction(() => adminApi.rebuildVfsClosures(), 'Дерево VFS перестроено')}>
                  Перестроить дерево
                </Btn>
              </div>
            }
          >
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <input
                value={vfsSearch}
                onChange={(e) => setVfsSearch(e.target.value)}
                placeholder="Поиск папки или трека…"
                style={{ background: 'var(--bg)', border: '1px solid var(--line)', color: 'var(--ink)', borderRadius: 8, padding: '8px 10px', minHeight: 40, flex: 1, minWidth: 180 }}
              />
              <select
                value={vfsFolderId}
                onChange={(e) => setVfsFolderId(e.target.value)}
                style={{ background: 'var(--bg)', border: '1px solid var(--line)', color: 'var(--ink)', borderRadius: 8, padding: '0 8px', minHeight: 40 }}
              >
                <option value="">Все треки</option>
                {vfsFolders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
              </select>
              {vfsSelectedFiles.length > 0 && vfsFolderId && (
                <Btn
                  variant="primary"
                  disabled={busy}
                  onClick={() => runVfsAction(() => adminApi.moveVfsFiles(vfsSelectedFiles, Number(vfsFolderId)), 'Треки перемещены')}
                >
                  Переместить выбранные
                </Btn>
              )}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 1fr) minmax(300px, 1.5fr)', gap: 14 }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 6 }}>Папки ({vfsFolders.length})</div>
                <div style={{ maxHeight: 420, overflow: 'auto' }}>
                  {vfsFolders.map((folder) => (
                    <div key={folder.id} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--line)', flexWrap: 'wrap' }}>
                      <button
                        type="button"
                        onClick={() => setVfsFolderId(String(folder.id))}
                        style={{ border: 'none', background: 'transparent', color: 'var(--ink)', cursor: 'pointer', textAlign: 'left', flex: 1, minWidth: 120, fontSize: 12 }}
                      >
                        {folder.is_trash ? '[корзина] ' : ''}{folder.is_starred ? '[избранное] ' : ''}{folder.name}
                        <span style={{ color: 'var(--dim)' }}> · {folder.track_count} треков</span>
                      </button>
                      <select
                        value={folder.parent_id || ''}
                        disabled={busy}
                        onChange={(e) => runVfsAction(() => adminApi.patchVfsFolder(folder.id, { set_parent: true, parent_id: e.target.value ? Number(e.target.value) : null }), 'Папка перемещена')}
                        style={{ maxWidth: 120, background: 'var(--bg)', color: 'var(--ink)', border: '1px solid var(--line)', borderRadius: 6, minHeight: 30, fontSize: 11 }}
                      >
                        <option value="">Корень</option>
                        {vfsFolders.filter((item) => item.id !== folder.id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                      </select>
                      <Btn disabled={busy} title="В корзину/восстановить" onClick={() => runVfsAction(() => adminApi.patchVfsFolder(folder.id, { is_trash: !folder.is_trash }), folder.is_trash ? 'Папка восстановлена' : 'Папка перемещена в корзину')}>
                        <Trash2 size={12} />
                      </Btn>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 6 }}>Треки ({vfsFiles.length})</div>
                <div style={{ maxHeight: 420, overflow: 'auto' }}>
                  {vfsFiles.map((file) => (
                    <div key={file.id} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--line)', fontSize: 12 }}>
                      <input
                        type="checkbox"
                        checked={vfsSelectedFiles.includes(file.id)}
                        onChange={(e) => setVfsSelectedFiles((prev) => e.target.checked ? [...prev, file.id] : prev.filter((id) => id !== file.id))}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {file.is_trash ? '[корзина] ' : ''}{file.is_starred ? '[избранное] ' : ''}{file.name}
                      </span>
                      <Btn disabled={busy} title="В корзину/восстановить" onClick={() => runVfsAction(() => adminApi.patchVfsFile(file.id, { is_trash: !file.is_trash }), file.is_trash ? 'Трек восстановлен' : 'Трек перемещён в корзину')}>
                        <Trash2 size={12} />
                      </Btn>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </Panel>
        </>
      )}

      {tab === 'settings' && settings && (
        <Panel title="Настройки приложения и бэкапов">
          <div style={{ display: 'grid', gap: 12, maxWidth: 420 }}>
            {[
              { key: 'backup_enabled', label: 'Автобэкапы включены', type: 'bool' },
              { key: 'backup_include_schema', label: 'Включать схему в dump', type: 'bool' },
              { key: 'maintenance_mode', label: 'Режим обслуживания (флаг)', type: 'bool' },
              {
                key: 'backup_interval_hours',
                label: 'Интервал бэкапа (часов)',
                type: 'number',
                min: 1,
                max: 720,
              },
              { key: 'backup_retention', label: 'Хранить файлов', type: 'number', min: 1, max: 365 },
              { key: 'backup_dir', label: 'Каталог бэкапов', type: 'text' },
              { key: 'site_title', label: 'Название проекта', type: 'text' },
            ].map((f) => (
              <label key={f.key} style={{ display: 'grid', gap: 4, fontSize: 12.5 }}>
                <span style={{ color: 'var(--dim)' }}>{f.label}</span>
                {f.type === 'bool' ? (
                  <input
                    type="checkbox"
                    checked={!!settings[f.key]}
                    onChange={(e) => setSettings({ ...settings, [f.key]: e.target.checked })}
                    style={{ width: 18, height: 18, accentColor: 'var(--accent)' }}
                  />
                ) : (
                  <input
                    type={f.type === 'number' ? 'number' : 'text'}
                    min={f.min}
                    max={f.max}
                    value={settings[f.key] ?? ''}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        [f.key]: f.type === 'number' ? Number(e.target.value) : e.target.value,
                      })
                    }
                    style={{
                      background: 'var(--bg)',
                      border: '1px solid var(--line)',
                      color: 'var(--ink)',
                      borderRadius: 8,
                      padding: '8px 10px',
                      minHeight: 40,
                      fontSize: 14,
                    }}
                  />
                )}
              </label>
            ))}
            <div>
              <Btn
                variant="primary"
                disabled={busy}
                onClick={() =>
                  runAction(() =>
                    adminApi.putSettings({
                      backup_enabled: !!settings.backup_enabled,
                      backup_include_schema: !!settings.backup_include_schema,
                      maintenance_mode: !!settings.maintenance_mode,
                      backup_interval_hours: Number(settings.backup_interval_hours) || 24,
                      backup_retention: Number(settings.backup_retention) || 14,
                      backup_dir: settings.backup_dir,
                      site_title: settings.site_title,
                    })
                  )
                }
              >
                Сохранить настройки
              </Btn>
            </div>
          </div>
        </Panel>
      )}

      {tab === 'users' && (
        <>
          <Panel title="Пользователи и роли">
            <div style={{ fontSize: 12, color: 'var(--dim)', marginBottom: 10 }}>
              Пока вход в админку — тот же пароль, что у пользователя <b>admin</b>.
              Роли: <code>admin</code> · <code>editor</code> · <code>user</code>. JWT не пересоздаётся
              автоматически после смены роли — перелогиньтесь.
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(users || []).map((u) => (
                <div
                  key={u.id}
                  style={{
                    display: 'flex',
                    gap: 8,
                    alignItems: 'center',
                    flexWrap: 'wrap',
                    padding: '8px 4px',
                    borderBottom: '1px solid var(--line)',
                    fontSize: 12.5,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 100, fontWeight: 700 }}>{u.username}</div>
                  <select
                    value={u.role}
                    onChange={async (e) => {
                      await runAction(() => adminApi.patchUser(u.id, { role: e.target.value }), 'Роль обновлена')
                    }}
                    style={{
                      background: 'var(--bg)',
                      color: 'var(--ink)',
                      border: '1px solid var(--line)',
                      borderRadius: 8,
                      minHeight: 36,
                      padding: '0 8px',
                    }}
                  >
                    <option value="admin">admin</option>
                    <option value="editor">editor</option>
                    <option value="user">user</option>
                  </select>
                  <Btn
                    onClick={() =>
                      runAction(
                        () => adminApi.patchUser(u.id, { is_active: !u.is_active }),
                        u.is_active ? 'Отключён' : 'Включён'
                      )
                    }
                  >
                    {u.is_active ? 'Active' : 'Disabled'}
                  </Btn>
                  <Btn
                    title="Разблокировать"
                    onClick={() => runAction(() => adminApi.unlockUser(u.id), 'Аккаунт разблокирован')}
                  >
                    <UnlockKeyhole size={12} />
                  </Btn>
                  <Btn
                    title="Сбросить пароль"
                    onClick={() => {
                      const password = window.prompt(`Новый пароль для ${u.username} (минимум 8 символов)`)
                      if (password && password.length >= 8) {
                        runAction(() => adminApi.patchUser(u.id, { new_password: password }), 'Пароль изменён')
                      }
                    }}
                  >
                    <KeyRound size={12} />
                  </Btn>
                  <Btn
                    title="Завершить все сессии"
                    onClick={() =>
                      runAction(() => adminApi.revokeUserSessions(u.id), 'Сессии завершены')
                    }
                  >
                    <ShieldAlert size={12} />
                  </Btn>
                  <Btn
                    variant="danger"
                    title="Удалить без данных"
                    onClick={() => {
                      if (window.confirm(`Удалить пользователя ${u.username}? Действие необратимо.`)) {
                        runAction(() => adminApi.deleteUser(u.id), 'Пользователь удалён')
                      }
                    }}
                  >
                    <Trash2 size={12} />
                  </Btn>
                </div>
              ))}
            </div>
          </Panel>
          <Panel title="Создать пользователя">
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <label style={{ fontSize: 12, display: 'grid', gap: 4 }}>
                Логин
                <input
                  value={newUser.username}
                  onChange={(e) => setNewUser({ ...newUser, username: e.target.value })}
                  style={{
                    background: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--ink)',
                    borderRadius: 8,
                    padding: '8px 10px',
                    minHeight: 40,
                    minWidth: 140,
                    fontSize: 14,
                  }}
                />
              </label>
              <label style={{ fontSize: 12, display: 'grid', gap: 4 }}>
                Пароль (min 8)
                <input
                  type="password"
                  value={newUser.password}
                  onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                  style={{
                    background: 'var(--bg)',
                    border: '1px solid var(--line)',
                    color: 'var(--ink)',
                    borderRadius: 8,
                    padding: '8px 10px',
                    minHeight: 40,
                    minWidth: 140,
                    fontSize: 14,
                  }}
                />
              </label>
              <label style={{ fontSize: 12, display: 'grid', gap: 4 }}>
                Роль
                <select
                  value={newUser.role}
                  onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
                  style={{
                    background: 'var(--bg)',
                    color: 'var(--ink)',
                    border: '1px solid var(--line)',
                    borderRadius: 8,
                    minHeight: 40,
                    padding: '0 8px',
                  }}
                >
                  <option value="user">user</option>
                  <option value="editor">editor</option>
                  <option value="admin">admin</option>
                </select>
              </label>
              <Btn
                variant="primary"
                disabled={busy || !newUser.username || newUser.password.length < 8}
                onClick={async () => {
                  await runAction(
                    () => adminApi.createUser(newUser),
                    `Пользователь ${newUser.username} создан`
                  )
                  setNewUser({ username: '', password: '', role: 'user' })
                }}
              >
                Создать
              </Btn>
            </div>
          </Panel>
        </>
      )}

      {tab === 'audit' && (
        <Panel title="Журнал административных действий">
          {!audit?.items?.length && <div style={{ color: 'var(--dim)', fontSize: 13 }}>Событий пока нет</div>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {(audit?.items || []).map((event) => (
              <div
                key={event.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(130px, 1fr) minmax(180px, 1.4fr) minmax(150px, 1fr)',
                  gap: 10,
                  padding: '8px 4px',
                  borderBottom: '1px solid var(--line)',
                  fontSize: 12,
                }}
              >
                <span style={{ color: 'var(--dim)' }}>{event.created_at}</span>
                <span><b>{event.actor}</b> · {event.action}</span>
                <span style={{ color: 'var(--dim)' }}>
                  {event.target_type ? `${event.target_type} #${event.target_id || '—'}` : ''}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}
