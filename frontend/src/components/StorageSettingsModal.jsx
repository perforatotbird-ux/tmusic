import React, { useState, useMemo } from 'react'
import {
  HardDrive,
  Trash2,
  X,
  Play,
  Check,
  Clock,
  Sliders,
  FolderDown,
  Layers,
  Music,
  Image as ImageIcon,
  FileText,
  AlertCircle,
  Search,
  CheckSquare,
  Square,
  Sparkles,
} from 'lucide-react'
import { useOfflineStore } from '../store/offlineStore'
import { usePlayerStore } from '../store/playerStore'
import Modal from './ui/Modal'

function formatBytes(bytes) {
  if (!bytes || bytes <= 0 || isNaN(bytes)) return '0 Б'
  const k = 1024
  const sizes = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`
}

function formatDate(timestamp) {
  if (!timestamp) return ''
  const d = new Date(timestamp)
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

const TTL_OPTIONS = [
  { value: 3, label: '3 дня' },
  { value: 7, label: '1 неделя' },
  { value: 30, label: '1 месяц' },
  { value: 0, label: 'Всегда' },
]

const SIZE_OPTIONS = [
  { value: 500, label: '500 МБ' },
  { value: 1024, label: '1 ГБ' },
  { value: 2048, label: '2 ГБ' },
  { value: 5120, label: '5 ГБ' },
  { value: 10240, label: '10 ГБ' },
  { value: 0, label: 'Без лимита' },
]

export function StorageSettingsModal({ onClose }) {
  const {
    settings,
    updateSettings,
    storageStats,
    cachedTracksList,
    removeTrack,
    clearCategory,
    clearAll,
  } = useOfflineStore()

  const { playQueue } = usePlayerStore()

  const [activeTab, setActiveTab] = useState('settings') // 'settings' | 'tracks'
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [clearSelection, setClearSelection] = useState({
    audio: true,
    covers: true,
    metadata: true,
  })
  const [isClearing, setIsClearing] = useState(false)

  const {
    audioBytes = 0,
    audioCount = 0,
    coversBytes = 0,
    coversCount = 0,
    metaBytes = 0,
    totalBytes = 0,
    deviceAvailableBytes = 0,
    deviceTotalBytes = 0,
  } = storageStats

  // Percentages for Telegram-style storage bar
  const totalBarSpace = Math.max(totalBytes, 1)
  const audioPct = (audioBytes / totalBarSpace) * 100
  const coversPct = (coversBytes / totalBarSpace) * 100
  const metaPct = (metaBytes / totalBarSpace) * 100

  // Filter cached tracks by search
  const filteredTracks = useMemo(() => {
    if (!searchQuery.trim()) return cachedTracksList
    const q = searchQuery.toLowerCase()
    return cachedTracksList.filter(
      (t) =>
        (t.title && t.title.toLowerCase().includes(q)) ||
        (t.name && t.name.toLowerCase().includes(q)) ||
        (t.artist && t.artist.toLowerCase().includes(q))
    )
  }, [cachedTracksList, searchQuery])

  const handleClearSelected = async () => {
    setIsClearing(true)
    try {
      if (clearSelection.audio && clearSelection.covers && clearSelection.metadata) {
        await clearAll()
      } else {
        if (clearSelection.audio) await clearCategory('audio')
        if (clearSelection.covers) await clearCategory('covers')
        if (clearSelection.metadata) await clearCategory('metadata')
      }
      setShowClearConfirm(false)
    } finally {
      setIsClearing(false)
    }
  }

  const handlePlayCachedTrack = (track) => {
    playQueue([track], 0, {
      title: track.title || track.name,
      artistName: track.artist || 'Офлайн кэш',
      coverUrl: track.coverUrl,
    })
  }

  const handlePlayAllCached = () => {
    if (filteredTracks.length === 0) return
    playQueue(filteredTracks, 0, {
      title: 'Закешированная музыка',
      artistName: 'Офлайн',
      coverUrl: null,
    })
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Данные и память"
      maxWidth={560}
      backdrop="rgba(0, 0, 0, 0.75)"
      backdropFilter="blur(8px)"
      style={{ background: 'transparent', border: 'none', boxShadow: 'none' }}
    >
      <div
        className="tmusic-storage-modal"
        style={{
          background: 'var(--bg-card, #1c1917)',
          border: '1px solid var(--line-bright, #2e2926)',
          borderRadius: '20px',
          width: '100%',
          maxWidth: '560px',
          maxHeight: '90dvh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 24px 60px rgba(0, 0, 0, 0.8)',
          overflow: 'hidden',
          color: 'var(--ink, #f5f5f4)',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '18px 20px',
            borderBottom: '1px solid var(--line, #2e2926)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '12px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '10px',
                background: 'var(--accent-muted, rgba(212, 255, 63, 0.15))',
                color: 'var(--accent, #d4ff3f)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <HardDrive size={20} />
            </div>
            <div>
              <h2 style={{ fontSize: '17px', fontWeight: '800', lineHeight: 1.2 }}>Данные и память</h2>
              <div style={{ fontSize: '12px', color: 'var(--dim, #a8a29e)' }}>
                Кэширование в стиле Telegram для мобильных
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--dim, #a8a29e)',
              cursor: 'pointer',
              padding: '6px',
              borderRadius: '8px',
              display: 'flex',
            }}
            aria-label="Закрыть"
          >
            <X size={20} />
          </button>
        </div>

        {/* Telegram-style Segmented Storage Overview Bar */}
        <div
          style={{
            padding: '16px 20px',
            background: 'rgba(0, 0, 0, 0.25)',
            borderBottom: '1px solid var(--line, #2e2926)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '8px' }}>
            <span style={{ fontSize: '13px', fontWeight: '600', color: 'var(--dim, #a8a29e)' }}>Занято кэшем:</span>
            <span style={{ fontSize: '20px', fontWeight: '800', color: 'var(--accent, #d4ff3f)' }}>
              {formatBytes(totalBytes)}
            </span>
          </div>

          {/* Segmented Bar */}
          <div
            style={{
              height: '10px',
              borderRadius: '6px',
              background: 'rgba(255, 255, 255, 0.08)',
              overflow: 'hidden',
              display: 'flex',
              marginBottom: '12px',
            }}
          >
            {totalBytes === 0 ? (
              <div style={{ width: '100%', background: 'rgba(255, 255, 255, 0.05)' }} />
            ) : (
              <>
                <div style={{ width: `${audioPct}%`, background: 'var(--success)', transition: 'width 0.3s' }} title={`Аудио: ${formatBytes(audioBytes)}`} />
                <div style={{ width: `${coversPct}%`, background: 'var(--info)', transition: 'width 0.3s' }} title={`Обложки: ${formatBytes(coversBytes)}`} />
                <div style={{ width: `${metaPct}%`, background: 'var(--warn)', transition: 'width 0.3s' }} title={`Метаданные: ${formatBytes(metaBytes)}`} />
              </>
            )}
          </div>

          {/* Legend */}
          <div
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: '14px',
              fontSize: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--success)' }} />
              <span>Музыка: <strong>{formatBytes(audioBytes)}</strong> ({audioCount} шт.)</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--info)' }} />
              <span>Обложки: <strong>{formatBytes(coversBytes)}</strong></span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--warn)' }} />
              <span>Тексты и каталог: <strong>{formatBytes(metaBytes)}</strong></span>
            </div>
            {deviceAvailableBytes > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--dim)' }}>
                <span>Свободно на диске: <strong>{formatBytes(deviceAvailableBytes)}</strong></span>
              </div>
            )}
          </div>
        </div>

        {/* Tab Switcher: Настройки vs Закешированные треки */}
        <div
          style={{
            display: 'flex',
            borderBottom: '1px solid var(--line, #2e2926)',
            background: 'var(--bg-card)',
          }}
        >
          <button
            onClick={() => setActiveTab('settings')}
            style={{
              flex: 1,
              padding: '12px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'settings' ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === 'settings' ? 'var(--accent)' : 'var(--dim)',
              fontWeight: '700',
              fontSize: '13.5px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
            }}
          >
            <Sliders size={16} /> Настройки кэша
          </button>
          <button
            onClick={() => setActiveTab('tracks')}
            style={{
              flex: 1,
              padding: '12px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'tracks' ? '2px solid var(--accent)' : '2px solid transparent',
              color: activeTab === 'tracks' ? 'var(--accent)' : 'var(--dim)',
              fontWeight: '700',
              fontSize: '13.5px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
            }}
          >
            <Music size={16} /> Музыка в кэше ({cachedTracksList.length})
          </button>
        </div>

        {/* Content Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '20px' }}>
          {activeTab === 'settings' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
              {/* Action: Clear Cache */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowClearConfirm((v) => !v)}
                  disabled={totalBytes === 0}
                  style={{
                    width: '100%',
                    padding: '12px 16px',
                    borderRadius: '12px',
                    background: totalBytes > 0 ? 'rgba(239, 68, 68, 0.12)' : 'rgba(255, 255, 255, 0.04)',
                    color: totalBytes > 0 ? 'var(--danger, #ef4444)' : 'var(--dim)',
                    border: '1px solid var(--line)',
                    fontWeight: '700',
                    fontSize: '14px',
                    cursor: totalBytes > 0 ? 'pointer' : 'not-allowed',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px',
                    transition: 'all 0.2s',
                  }}
                >
                  <Trash2 size={16} /> Очистить кэш...
                </button>

                {/* Clear Breakdown Popup */}
                {showClearConfirm && (
                  <div
                    style={{
                      marginTop: '12px',
                      padding: '14px',
                      background: 'rgba(0,0,0,0.3)',
                      borderRadius: '12px',
                      border: '1px solid var(--line)',
                    }}
                  >
                    <div style={{ fontSize: '13px', fontWeight: '700', marginBottom: '10px' }}>
                      Выберите данные для удаления:
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginBottom: '8px', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={clearSelection.audio}
                        onChange={(e) => setClearSelection((s) => ({ ...s, audio: e.target.checked }))}
                      />
                      <span>Треки ({formatBytes(audioBytes)}, {audioCount} шт.)</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginBottom: '8px', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={clearSelection.covers}
                        onChange={(e) => setClearSelection((s) => ({ ...s, covers: e.target.checked }))}
                      />
                      <span>Обложки альбомов ({formatBytes(coversBytes)})</span>
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginBottom: '12px', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={clearSelection.metadata}
                        onChange={(e) => setClearSelection((s) => ({ ...s, metadata: e.target.checked }))}
                      />
                      <span>Тексты и структура каталога ({formatBytes(metaBytes)})</span>
                    </label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        type="button"
                        onClick={handleClearSelected}
                        disabled={isClearing || (!clearSelection.audio && !clearSelection.covers && !clearSelection.metadata)}
                        style={{
                          flex: 1,
                          padding: '8px 12px',
                          borderRadius: '8px',
                          background: 'var(--danger, #ef4444)',
                          color: '#fff',
                          border: 'none',
                          fontWeight: '700',
                          fontSize: '13px',
                          cursor: 'pointer',
                        }}
                      >
                        {isClearing ? 'Очистка...' : 'Очистить выбранное'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowClearConfirm(false)}
                        style={{
                          padding: '8px 12px',
                          borderRadius: '8px',
                          background: 'transparent',
                          color: 'var(--ink)',
                          border: '1px solid var(--line)',
                          fontWeight: '600',
                          fontSize: '13px',
                          cursor: 'pointer',
                        }}
                      >
                        Отмена
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Setting 1: Cache TTL (Время жизни) */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                  <Clock size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ fontSize: '14px', fontWeight: '800' }}>Хранить медиа (Время жизни)</span>
                </div>
                <p style={{ fontSize: '12.5px', color: 'var(--dim)', marginBottom: '10px', lineHeight: 1.4 }}>
                  Файлы, к которым не было обращений дольше выбранного срока, будут автоматически удалены из кэша для экономии места на телефоне.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
                  {TTL_OPTIONS.map((opt) => {
                    const active = settings.cacheTtlDays === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => updateSettings({ cacheTtlDays: opt.value })}
                        style={{
                          padding: '10px 6px',
                          borderRadius: '10px',
                          border: active ? '2px solid var(--accent)' : '1px solid var(--line)',
                          background: active ? 'var(--accent-muted)' : 'rgba(255,255,255,0.03)',
                          color: active ? 'var(--accent)' : 'var(--ink)',
                          fontSize: '13px',
                          fontWeight: active ? '800' : '600',
                          cursor: 'pointer',
                          textAlign: 'center',
                          transition: 'all 0.15s',
                        }}
                      >
                        {opt.label}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* Setting 2: Cache Size Limit (Размер кэша) */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                  <Sliders size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ fontSize: '14px', fontWeight: '800' }}>Максимальный размер кэша</span>
                </div>
                <p style={{ fontSize: '12.5px', color: 'var(--dim)', marginBottom: '10px', lineHeight: 1.4 }}>
                  При достижении лимита самые старые прослушанные треки будут вытеснены первыми (LRU), освобождая место под новые.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                  {SIZE_OPTIONS.map((opt) => {
                    const active = settings.maxCacheSizeMb === opt.value
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => updateSettings({ maxCacheSizeMb: opt.value })}
                        style={{
                          padding: '10px 6px',
                          borderRadius: '10px',
                          border: active ? '2px solid var(--accent)' : '1px solid var(--line)',
                          background: active ? 'var(--accent-muted)' : 'rgba(255,255,255,0.03)',
                          color: active ? 'var(--accent)' : 'var(--ink)',
                          fontSize: '13px',
                          fontWeight: active ? '800' : '600',
                          cursor: 'pointer',
                          textAlign: 'center',
                          transition: 'all 0.15s',
                        }}
                      >
                        {opt.label}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* Setting 3: What to cache (Какие данные кешировать) */}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                  <FolderDown size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ fontSize: '14px', fontWeight: '800' }}>Какие данные кешировать</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '10px' }}>
                  {/* Auto cache on play */}
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: '12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: '700', fontSize: '13.5px' }}>Автоматически кешировать треки</div>
                      <div style={{ fontSize: '12px', color: 'var(--dim)' }}>
                        Сохранять прослушиваемые треки для офлайн-плеера как в Telegram
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={settings.autoCacheOnPlay}
                      onChange={(e) => updateSettings({ autoCacheOnPlay: e.target.checked })}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                    />
                  </label>

                  {/* Cache Audio */}
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: '12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: '700', fontSize: '13.5px' }}>Аудиофайлы (MP3 / FLAC)</div>
                      <div style={{ fontSize: '12px', color: 'var(--dim)' }}>
                        Разрешить сохранение аудиозаписей в памяти устройства
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={settings.cacheAudio}
                      onChange={(e) => updateSettings({ cacheAudio: e.target.checked })}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                    />
                  </label>

                  {/* Cache Covers */}
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: '12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: '700', fontSize: '13.5px' }}>Обложки альбомов и карточек</div>
                      <div style={{ fontSize: '12px', color: 'var(--dim)' }}>
                        Быстрая отрисовка картинок без повторных запросов к сети
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={settings.cacheCovers}
                      onChange={(e) => updateSettings({ cacheCovers: e.target.checked })}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                    />
                  </label>

                  {/* Cache Lyrics */}
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: '12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: '700', fontSize: '13.5px' }}>Тексты песен (LRC)</div>
                      <div style={{ fontSize: '12px', color: 'var(--dim)' }}>
                        Синхронизированные слова песен доступны офлайн
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={settings.cacheLyrics}
                      onChange={(e) => updateSettings({ cacheLyrics: e.target.checked })}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                    />
                  </label>

                  {/* Cache Catalog */}
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 14px',
                      borderRadius: '12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid var(--line)',
                      cursor: 'pointer',
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: '700', fontSize: '13.5px' }}>Плейлисты и каталог</div>
                      <div style={{ fontSize: '12px', color: 'var(--dim)' }}>
                        Сохранение структуры плейлистов для работы без интернета
                      </div>
                    </div>
                    <input
                      type="checkbox"
                      checked={settings.cacheCatalog}
                      onChange={(e) => updateSettings({ cacheCatalog: e.target.checked })}
                      style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }}
                    />
                  </label>
                </div>
              </div>
            </div>
          ) : (
            /* Tab: Cached Tracks List */
            <div>
              {/* Search & Actions Bar */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '14px' }}>
                <div
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 12px',
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid var(--line)',
                    borderRadius: '10px',
                  }}
                >
                  <Search size={16} style={{ color: 'var(--dim)' }} />
                  <input
                    type="text"
                    placeholder="Поиск по закешированным трекам..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--ink)',
                      fontSize: '13px',
                      width: '100%',
                    }}
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      style={{ background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer' }}
                    >
                      <X size={14} />
                    </button>
                  )}
                </div>

                {filteredTracks.length > 0 && (
                  <button
                    type="button"
                    onClick={handlePlayAllCached}
                    style={{
                      padding: '8px 14px',
                      borderRadius: '10px',
                      background: 'var(--accent)',
                      color: 'var(--on-accent)',
                      border: 'none',
                      fontWeight: '800',
                      fontSize: '13px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      flexShrink: 0,
                    }}
                  >
                    <Play size={14} fill="currentColor" /> Слушать всё
                  </button>
                )}
              </div>

              {/* List */}
              {filteredTracks.length === 0 ? (
                <div
                  style={{
                    textAlign: 'center',
                    padding: '40px 20px',
                    color: 'var(--dim)',
                  }}
                >
                  <Music size={36} style={{ opacity: 0.3, marginBottom: '8px' }} />
                  <div style={{ fontWeight: '700', fontSize: '15px', color: 'var(--ink)', marginBottom: '4px' }}>
                    {searchQuery ? 'Ничего не найдено' : 'Кэш пуст'}
                  </div>
                  <div style={{ fontSize: '13px' }}>
                    {searchQuery
                      ? 'Попробуйте изменить поисковый запрос'
                      : 'Включите автокеширование или нажимайте «Сохранить в кэш» на любимых треках.'}
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {filteredTracks.map((track) => (
                    <div
                      key={track.fileId}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 12px',
                        borderRadius: '10px',
                        background: 'rgba(255,255,255,0.02)',
                        border: '1px solid var(--line)',
                        gap: '10px',
                      }}
                    >
                      {/* Play button */}
                      <button
                        type="button"
                        onClick={() => handlePlayCachedTrack(track)}
                        style={{
                          width: '32px',
                          height: '32px',
                          borderRadius: '8px',
                          background: 'var(--accent-muted)',
                          color: 'var(--accent)',
                          border: 'none',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          cursor: 'pointer',
                          flexShrink: 0,
                        }}
                        title="Играть офлайн"
                      >
                        <Play size={15} fill="var(--accent)" />
                      </button>

                      {/* Info */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            fontWeight: '700',
                            fontSize: '13.5px',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {track.title || track.name}
                        </div>
                        <div
                          style={{
                            fontSize: '11.5px',
                            color: 'var(--dim)',
                            display: 'flex',
                            gap: '8px',
                            alignItems: 'center',
                          }}
                        >
                          {track.artist && <span>{track.artist}</span>}
                          <span>•</span>
                          <span>{formatBytes(track.size)}</span>
                          {track.cachedAt && (
                            <>
                              <span>•</span>
                              <span>сохранён {formatDate(track.cachedAt)}</span>
                            </>
                          )}
                        </div>
                      </div>

                      {/* Delete from cache */}
                      <button
                        type="button"
                        onClick={() => removeTrack(track.fileId)}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--dim)',
                          padding: '6px',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          display: 'flex',
                        }}
                        title="Удалить из кэша"
                        aria-label="Удалить из кэша"
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}
