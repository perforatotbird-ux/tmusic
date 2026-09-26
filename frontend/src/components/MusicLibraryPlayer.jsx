import React, { useEffect, useMemo, useRef, useState } from 'react'
import { usePlayerStore } from '../store/playerStore'
import { useFavoritesStore } from '../store/favoritesStore'
import { useAuthStore } from '../store/authStore'
import { resolveReleaseCoverUrl } from '../utils/cover'
import { metadataApi } from '../api/metadata'
import { parseLrc, findLrcIndex, plainLines } from '../utils/lrc'
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Shuffle,
  Repeat,
  Repeat1,
  Maximize2,
  ListMusic,
  ChevronDown,
  Trash2,
  Star,
  FileText,
  X,
  ArrowUp,
  ArrowDown,
  Loader2,
  ListEnd,
} from 'lucide-react'

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

/**
 * Industry-style scrubbing: the handle follows the pointer while dragging and
 * `seek` is committed only on release. A single mid-stream drag otherwise
 * fires dozens of Range requests against the Telegram source.
 */
function useScrub(duration, currentTime, seek) {
  const draggingRef = useRef(false)
  const [drag, setDrag] = useState(false)
  const [val, setVal] = useState(0)
  const max = Number.isFinite(duration) && duration > 0 ? duration : 0
  const shown = drag ? val : currentTime || 0

  const onPointerDown = (e) => {
    if (!max) return
    draggingRef.current = true
    setDrag(true)
    setVal(e.currentTarget.valueAsNumber || 0)
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      /* synthetic events / unsupported */
    }
  }
  const onChange = (e) => {
    const v = e.currentTarget.valueAsNumber
    if (draggingRef.current) setVal(v)
    else seek(v) // keyboard arrows: commit per keypress
  }
  const endDrag = () => {
    if (!draggingRef.current) return
    draggingRef.current = false
    setDrag(false)
    if (max) seek(val)
  }

  return {
    max,
    shown,
    bind: {
      onPointerDown,
      onChange,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onLostPointerCapture: endDrag,
    },
  }
}

/** Range slider with track / buffered / progress layers under a transparent input. */
function Scrubber({ duration, currentTime, bufferedUpTo, seek, label }) {
  const { max, shown, bind } = useScrub(duration, currentTime, seek)
  const pct = max > 0 ? Math.min(100, (shown / max) * 100) : 0
  const bufPct = max > 0 ? Math.min(100, ((bufferedUpTo || 0) / max) * 100) : 0

  return (
    <div className="tm-scrub">
      <div className="tm-scrub-track" aria-hidden="true">
        <div className="tm-scrub-buffer" style={{ width: `${bufPct}%` }} />
        <div className="tm-scrub-fill" style={{ width: `${pct}%` }} />
      </div>
      <input
        type="range"
        className="tm-scrub-input"
        min="0"
        max={max || 100}
        step="1"
        value={Math.min(shown, max || 0)}
        disabled={!max}
        aria-label={label || 'Позиция воспроизведения'}
        aria-valuetext={`${formatTime(shown)} из ${formatTime(max)}`}
        {...bind}
      />
    </div>
  )
}

function EqBars() {
  return (
    <span className="tm-eq" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  )
}

/** Mute toggle + slider bound to the store — reused across every player mode. */
function VolumeControl({ className = '' }) {
  const volume = usePlayerStore((s) => s.volume)
  const isMuted = usePlayerStore((s) => s.isMuted)
  const setVolume = usePlayerStore((s) => s.setVolume)
  const toggleMute = usePlayerStore((s) => s.toggleMute)
  const silent = isMuted || volume === 0
  return (
    <div className={`tm-volume ${className}`} onClick={(e) => e.stopPropagation()}>
      <button
        onClick={toggleMute}
        aria-label={silent ? 'Включить звук' : 'Выключить звук'}
        title={silent ? 'Включить звук (M)' : 'Выключить звук (M)'}
      >
        {silent ? <VolumeX size={16} /> : <Volume2 size={16} />}
      </button>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={silent ? 0 : volume}
        onChange={(e) => setVolume(parseFloat(e.target.value))}
        aria-label="Громкость"
        aria-valuetext={`${Math.round((silent ? 0 : volume) * 100)}%`}
      />
    </div>
  )
}

export function MusicLibraryPlayer() {
  const [showQueue, setShowQueue] = useState(false)
  const [showLyrics, setShowLyrics] = useState(false)
  const [lyrics, setLyrics] = useState(null) // LyricsResponse | null
  const [lyricsLoading, setLyricsLoading] = useState(false)
  const [lyricsError, setLyricsError] = useState(null)
  const lyricsSeq = useRef(0)
  const lyricsScrollRef = useRef(null)
  const [swipeY, setSwipeY] = useState(0)
  const swipeStart = useRef(null)
  const {
    currentTrack,
    currentRelease,
    queue,
    queueIndex,
    isPlaying,
    isBuffering,
    currentTime,
    duration,
    bufferedUpTo,
    shuffle,
    repeat,
    isPlayerExpanded,
    notice,
    durations,
    togglePlay,
    forceResume,
    nextTrack,
    prevTrack,
    seek,
    toggleShuffle,
    cycleRepeat,
    setPlayerExpanded,
    playQueue,
    removeFromQueue,
    moveQueueItem,
    clearUpcoming,
    dismissNotice,
  } = usePlayerStore()
  const { isAuthenticated } = useAuthStore()
  const favIds = useFavoritesStore((s) => s.ids)
  const loadFavorites = useFavoritesStore((s) => s.load)
  const toggleFavorite = useFavoritesStore((s) => s.toggle)

  useEffect(() => {
    if (isAuthenticated) loadFavorites()
  }, [isAuthenticated, loadFavorites])

  // Fullscreen: Esc closes, body scroll locked (the overlay owns the screen).
  useEffect(() => {
    if (!isPlayerExpanded) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') setPlayerExpanded(false)
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
      setSwipeY(0)
    }
  }, [isPlayerExpanded, setPlayerExpanded])

  // Error/notice toast auto-fades like the library toast.
  useEffect(() => {
    if (!notice) return undefined
    const t = setTimeout(dismissNotice, 6000)
    return () => clearTimeout(t)
  }, [notice, dismissNotice])

  // Fetch lyrics when panel is open and track changes (or on first open).
  const trackId = currentTrack?.id ?? null
  const trackName = currentTrack?.name ?? ''
  useEffect(() => {
    if (!showLyrics || !trackId || !isAuthenticated) return undefined
    const seq = ++lyricsSeq.current
    setLyricsLoading(true)
    setLyricsError(null)
    ;(async () => {
      try {
        const release = usePlayerStore.getState().currentRelease
        const artistName = release?.artistName
        const artist =
          artistName && artistName !== 'T-Music' && artistName !== 'Недавнее'
            ? artistName
            : null
        const base = trackName.replace(/\.[^/.]+$/, '')
        // When artist comes from the release context, strip "Artist - " from the title hint.
        let trackHint = base
        if (artist && base.includes(' - ')) {
          trackHint = base.split(' - ').slice(1).join(' - ').trim()
        }
        const res = await metadataApi.lyrics({
          artist,
          track: artist ? trackHint : undefined,
          duration: usePlayerStore.getState().duration || null,
          fileName: trackName,
        })
        if (seq !== lyricsSeq.current) return
        setLyrics(res?.found ? res : null)
      } catch (e) {
        if (seq !== lyricsSeq.current) return
        setLyrics(null)
        setLyricsError(e?.message || 'Не удалось загрузить текст')
      } finally {
        if (seq === lyricsSeq.current) setLyricsLoading(false)
      }
    })()
    return () => {
      /* seq guard cancels stale writes */
    }
  }, [showLyrics, trackId, trackName, isAuthenticated])

  const lrcLines = useMemo(() => parseLrc(lyrics?.synced || ''), [lyrics])
  const activeLine = useMemo(
    () => (lrcLines.length ? findLrcIndex(lrcLines, currentTime) : -1),
    [lrcLines, currentTime]
  )
  const displayLines = useMemo(() => {
    if (lrcLines.length) return lrcLines.map((l) => l.text || ' ')
    return plainLines(lyrics?.plain || '')
  }, [lrcLines, lyrics])

  // Keep active LRC line visible while playing.
  useEffect(() => {
    if (!showLyrics || activeLine < 0 || !lyricsScrollRef.current) return
    const el = lyricsScrollRef.current.querySelector(`[data-lrc="${activeLine}"]`)
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }, [showLyrics, activeLine])

  // ─── Queue editing state (draggable mirror of the store queue) ────────────
  // These hooks MUST sit above the `if (!currentTrack) return null` guard:
  // currentTrack toggles null↔track mid-session and hook order has to be
  // identical every render, or React throws #310 (fewer hooks than before).
  const [qView, setQView] = useState(null)
  const dragFromRef = useRef(null)
  const dragActiveRef = useRef(false)
  useEffect(() => {
    if (!showQueue) setQView(null)
  }, [showQueue])
  useEffect(() => {
    setQView(null)
  }, [queueIndex, queue.length])

  if (!currentTrack) return null

  const trackTitle = (currentTrack.name || 'Без названия').replace(/\.[^/.]+$/, '')
  const artistName = currentRelease?.artistName || 'T-Music'
  const albumTitle = currentRelease?.title || ''
  const coverUrl = resolveReleaseCoverUrl(currentRelease)
  const isFav = favIds.has(currentTrack.id)
  const sideMode = showLyrics ? 'lyrics' : showQueue ? 'queue' : 'art'

  // Android: store may say "playing" while <audio> is paused after lockscreen.
  const handlePlayPause = () => {
    const audio = usePlayerStore.getState().audioElement
    if (isPlaying && audio?.paused) {
      forceResume()
      return
    }
    togglePlay()
  }

  const handleToggleFav = (e) => {
    e?.stopPropagation?.()
    if (currentTrack?.id) toggleFavorite(currentTrack.id)
  }

  // ─── Queue editing (draggable mirror of the store queue) ────────────────
  // (Hooks live above the early return; only the derived mirror goes here.)
  const rows = qView || queue.map((t, i) => ({ t, i }))

  const onRowDragStart = (e, srcIdx) => {
    if (srcIdx <= queueIndex) {
      e.preventDefault()
      return // only upcoming items reorder
    }
    e.dataTransfer.effectAllowed = 'move'
    dragActiveRef.current = true
    dragFromRef.current = srcIdx
    if (!qView) setQView(queue.map((t, i) => ({ t, i })))
  }
  const onRowDragOver = (e, viewIdx) => {
    if (!dragActiveRef.current || !qView) return
    e.preventDefault()
    const from = qView.findIndex((r) => r.i === dragFromRef.current)
    if (from === -1 || from === viewIdx) return
    const next = [...qView]
    const [item] = next.splice(from, 1)
    next.splice(viewIdx, 0, item)
    setQView(next)
  }
  const onRowDragEnd = () => {
    if (!dragActiveRef.current) return
    dragActiveRef.current = false
    const from = dragFromRef.current
    const to = qView ? qView.findIndex((r) => r.i === from) : -1
    setQView(null)
    if (from != null && to >= 0 && to !== from) moveQueueItem(from, to)
  }

  // ─── Swipe-to-close on the fullscreen header / art column ───────────────
  const swipeProps = {
    onTouchStart: (e) => {
      if (e.target.closest('.tm-scrub, button, .tmusic-nowplaying-queue')) return
      swipeStart.current = e.touches[0].clientY
    },
    onTouchMove: (e) => {
      if (swipeStart.current == null) return
      const dy = e.touches[0].clientY - swipeStart.current
      if (dy > 0) setSwipeY(Math.min(dy * 0.85, 220))
    },
    onTouchEnd: () => {
      if (swipeY > 90) setPlayerExpanded(false)
      setSwipeY(0)
      swipeStart.current = null
    },
  }

  return (
    <>
      {/* ─── Compact Bottom Player Bar ────────────────────────────────────────── */}
      <div
        className="tmusic-playerbar"
        onClick={(e) => {
          if (!e.target.closest('button') && !e.target.closest('input') && !e.target.closest('.tm-scrub')) {
            setPlayerExpanded(true)
          }
        }}
      >
        {/* Top-edge mini progress bar (streaming style indicator on mobile) */}
        <div className="tmusic-player-minibar">
          <div
            className="tmusic-player-minibar-fill"
            style={{
              width: `${Math.min(100, Math.max(0, ((currentTime || 0) / (duration || 1)) * 100))}%`,
            }}
          />
        </div>

        {/* Track Info */}
        <div className="tmusic-player-info" onClick={() => setPlayerExpanded(true)} style={{ cursor: 'pointer' }}>
          <div style={{ position: 'relative', flexShrink: 0 }}>
            <img
              src={coverUrl}
              alt="Cover"
              style={{
                width: '46px',
                height: '46px',
                borderRadius: '6px',
                objectFit: 'cover',
                cursor: 'pointer',
                border: '1px solid var(--line)',
                display: 'block',
              }}
            />
            {isBuffering && (
              <span className="tm-buffer-pip" aria-hidden="true">
                <Loader2 size={10} className="tmusic-spin" />
              </span>
            )}
          </div>
          <div style={{ overflow: 'hidden', minWidth: 0 }}>
            <div
              style={{
                color: 'var(--ink)',
                fontWeight: '600',
                fontSize: '13.5px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {trackTitle}
            </div>
            <div
              style={{
                color: 'var(--dim)',
                fontSize: '12px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {artistName} {albumTitle && `• ${albumTitle}`}
            </div>
          </div>
        </div>

        {/* Mobile Mini-Player Action Buttons */}
        <div className="tmusic-player-mobile-actions" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={handlePlayPause}
            aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
            style={{
              width: '42px',
              height: '42px',
              borderRadius: '50%',
              backgroundColor: 'var(--accent)',
              color: '#141210',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            {isBuffering ? (
              <Loader2 size={19} className="tmusic-spin" />
            ) : isPlaying ? (
              <Pause size={20} fill="#141210" />
            ) : (
              <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />
            )}
          </button>
          <button
            onClick={nextTrack}
            aria-label="Следующий трек"
            style={{
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              background: 'none',
              border: 'none',
              color: 'var(--ink)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <SkipForward size={20} />
          </button>
          <VolumeControl className="tm-compact-volume" />
          <button
            onClick={() => setPlayerExpanded(true)}
            aria-label="Развернуть плеер"
            style={{
              width: '40px',
              height: '40px',
              borderRadius: '50%',
              background: 'none',
              border: 'none',
              color: 'var(--dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              flexShrink: 0,
            }}
          >
            <Maximize2 size={18} />
          </button>
        </div>

        {/* Center Controls & Scrubber (Desktop) */}
        <div className="tmusic-player-center">
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <button
              className="tmusic-hide-mobile"
              onClick={toggleShuffle}
              aria-label="Перемешать"
              aria-pressed={shuffle}
              title="Перемешать"
              style={{
                background: 'none',
                border: 'none',
                color: shuffle ? 'var(--accent)' : 'var(--dim)',
                cursor: 'pointer',
                display: 'flex',
                padding: 4,
              }}
            >
              <Shuffle size={15} />
            </button>
            <button
              onClick={prevTrack}
              aria-label="Предыдущий трек"
              title="Предыдущий трек"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--ink)',
                cursor: 'pointer',
                display: 'flex',
                padding: 4,
              }}
            >
              <SkipBack size={18} />
            </button>
            <button
              onClick={handlePlayPause}
              aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
              title={isPlaying ? 'Пауза (Space)' : 'Играть (Space)'}
              style={{
                width: '38px',
                height: '38px',
                borderRadius: '50%',
                backgroundColor: 'var(--accent)',
                color: '#141210',
                border: 'none',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                transition: 'transform 0.1s ease',
                flexShrink: 0,
              }}
              onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.92)')}
              onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
            >
              {isBuffering ? (
                <Loader2 size={18} className="tmusic-spin" />
              ) : isPlaying ? (
                <Pause size={18} fill="#141210" />
              ) : (
                <Play size={18} fill="#141210" style={{ marginLeft: '2px' }} />
              )}
            </button>
            <button
              onClick={nextTrack}
              aria-label="Следующий трек"
              title="Следующий трек"
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--ink)',
                cursor: 'pointer',
                display: 'flex',
                padding: 4,
              }}
            >
              <SkipForward size={18} />
            </button>
            <button
              className="tmusic-hide-mobile"
              onClick={cycleRepeat}
              aria-label="Повтор"
              title={repeat === 'off' ? 'Повтор выключен' : repeat === 'all' ? 'Повтор очереди' : 'Повтор трека'}
              style={{
                background: 'none',
                border: 'none',
                color: repeat !== 'off' ? 'var(--accent)' : 'var(--dim)',
                cursor: 'pointer',
                display: 'flex',
                padding: 4,
              }}
            >
              {repeat === 'one' ? <Repeat1 size={15} /> : <Repeat size={15} />}
            </button>
            {isAuthenticated && (
              <button
                className="tmusic-hide-mobile"
                onClick={handleToggleFav}
                title={isFav ? 'Убрать из любимых (L)' : 'В любимые (L)'}
                aria-label={isFav ? 'Убрать из любимых' : 'В любимые'}
                aria-pressed={isFav}
                style={{
                  background: 'none',
                  border: 'none',
                  color: isFav ? 'var(--accent)' : 'var(--dim)',
                  cursor: 'pointer',
                  display: 'flex',
                  padding: 4,
                }}
              >
                <Star size={15} fill={isFav ? 'var(--accent)' : 'none'} />
              </button>
            )}
          </div>

          {/* Scrubber */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', width: '100%' }}>
            <span style={{ fontSize: '11px', color: 'var(--dim)', minWidth: '40px', textAlign: 'right' }}>
              {formatTime(currentTime)}
            </span>
            <Scrubber duration={duration} currentTime={currentTime} bufferedUpTo={bufferedUpTo} seek={seek} />
            <span style={{ fontSize: '11px', color: 'var(--dim)', minWidth: '40px' }}>
              {formatTime(duration)}
            </span>
          </div>
        </div>

        {/* Right Tools (Volume & Expand - Desktop) */}
        <div className="tmusic-player-tools">
          <button
            className="tmusic-hide-mobile"
            onClick={() => {
              setShowQueue(true)
              setShowLyrics(false)
              setPlayerExpanded(true)
            }}
            aria-label="Очередь воспроизведения"
            title="Очередь"
            style={{
              background: 'none',
              border: 'none',
              color: showQueue ? 'var(--accent)' : 'var(--dim)',
              cursor: 'pointer',
              display: 'flex',
              padding: 4,
            }}
          >
            <ListMusic size={17} />
          </button>
          <VolumeControl className="tmusic-player-volume" />
          <button
            onClick={() => setPlayerExpanded(true)}
            aria-label="Развернуть плеер"
            title="Развернуть плеер"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--dim)',
              cursor: 'pointer',
              display: 'flex',
              padding: 4,
            }}
          >
            <Maximize2 size={17} />
          </button>
        </div>
      </div>

      {/* ─── Fullscreen Now Playing Overlay ──────────────────────────────────── */}
      {isPlayerExpanded && (
        <div
          className="tmusic-nowplaying"
          style={{
            zIndex: 9500,
            transform: swipeY > 0 ? `translateY(${swipeY}px)` : undefined,
            opacity: swipeY > 0 ? Math.max(0.4, 1 - swipeY / 420) : undefined,
            transition: swipeY > 0 ? 'none' : undefined,
          }}
        >
          {/* Top Bar */}
          <div
            className="tmusic-nowplaying-topbar"
            style={{ borderBottom: '1px solid var(--line)' }}
            {...swipeProps}
          >
            <button
              onClick={() => setPlayerExpanded(false)}
              className="tmusic-np-btn"
              aria-label="Свернуть"
              title="Свернуть (Esc)"
            >
              <ChevronDown size={28} />
            </button>
            <div style={{ textAlign: 'center', overflow: 'hidden', padding: '0 8px', flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '10px', color: 'var(--accent)', fontWeight: '700', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                СЕЙЧАС ИГРАЕТ
              </div>
              <div style={{ fontSize: '13px', color: 'var(--ink)', fontWeight: '600', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {trackTitle}
              </div>
            </div>
            <button
              onClick={() => {
                setShowLyrics(false)
                setShowQueue((prev) => !prev)
              }}
              className={`tmusic-np-btn ${showQueue ? 'active' : ''}`}
              aria-label="Очередь воспроизведения"
              aria-pressed={showQueue}
              title={showQueue ? 'Показать обложку' : 'Показать очередь'}
            >
              <ListMusic size={22} />
            </button>
            {isAuthenticated && (
              <button
                onClick={() => {
                  setShowQueue(false)
                  setShowLyrics((prev) => !prev)
                }}
                className={`tmusic-np-btn ${showLyrics ? 'active' : ''}`}
                aria-label="Текст песни"
                aria-pressed={showLyrics}
                title={showLyrics ? 'Скрыть текст' : 'Текст песни'}
              >
                <FileText size={20} />
              </button>
            )}
          </div>

          {/* Main Content Grid */}
          <div className="tmusic-nowplaying-body">
            {/* Left Column: Artwork & Track Meta */}
            <div className={`tmusic-nowplaying-art ${sideMode !== 'art' ? 'mobile-hidden' : ''}`} {...swipeProps}>
              <div style={{ position: 'relative', width: '100%', maxWidth: '360px' }}>
                <img
                  src={coverUrl}
                  alt="Cover Full"
                  style={{
                    width: '100%',
                    maxWidth: '360px',
                    aspectRatio: '1/1',
                    borderRadius: '16px',
                    objectFit: 'cover',
                    boxShadow: '0 25px 50px rgba(0,0,0,0.7)',
                    border: '1px solid var(--line-bright)',
                    display: 'block',
                  }}
                />
                {isBuffering && (
                  <span className="tm-buffer-badge">
                    <Loader2 size={13} className="tmusic-spin" /> Буферизация
                  </span>
                )}
              </div>
              <h2
                style={{
                  fontSize: '20px',
                  fontWeight: '700',
                  color: 'var(--ink)',
                  textAlign: 'center',
                  margin: '20px 0 4px',
                  maxWidth: '100%',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  padding: '0 8px',
                }}
              >
                {trackTitle}
              </h2>
              <div style={{ fontSize: '14.5px', color: 'var(--accent)', marginBottom: '2px', fontWeight: '600' }}>
                {artistName}
              </div>
              <div style={{ fontSize: '12.5px', color: 'var(--dim)', marginBottom: '18px' }}>
                {albumTitle}
              </div>

              {/* Favorite toggle */}
              {isAuthenticated && (
                <button
                  onClick={handleToggleFav}
                  className="tmusic-np-btn"
                  style={{
                    color: isFav ? 'var(--accent)' : 'var(--dim)',
                    marginBottom: '14px',
                    width: 'auto',
                    borderRadius: '999px',
                    padding: '0 14px',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '8px',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '13px',
                    fontWeight: 600,
                  }}
                  title={isFav ? 'Убрать из любимых' : 'В любимые'}
                  aria-pressed={isFav}
                >
                  <Star size={18} fill={isFav ? 'var(--accent)' : 'none'} />
                  {isFav ? 'В любимых' : 'В любимые'}
                </button>
              )}

              {/* Progress Slider */}
              <div style={{ width: '100%', maxWidth: '380px', marginBottom: '16px' }}>
                <Scrubber duration={duration} currentTime={currentTime} bufferedUpTo={bufferedUpTo} seek={seek} />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--dim)', marginTop: '6px' }}>
                  <span>{formatTime(currentTime)}</span>
                  <span>{formatTime(duration)}</span>
                </div>
              </div>

              {/* Big Controls */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '22px' }}>
                <button
                  onClick={toggleShuffle}
                  className="tmusic-np-btn"
                  style={{ color: shuffle ? 'var(--accent)' : 'var(--dim)' }}
                  aria-label="Перемешать"
                  aria-pressed={shuffle}
                  title="Перемешать"
                >
                  <Shuffle size={20} />
                </button>
                <button
                  onClick={prevTrack}
                  className="tmusic-np-btn"
                  style={{ color: 'var(--ink)' }}
                  aria-label="Предыдущий трек"
                  title="Предыдущий"
                >
                  <SkipBack size={26} />
                </button>
                <button
                  onClick={handlePlayPause}
                  aria-label={isPlaying ? 'Пауза' : 'Воспроизведение'}
                  style={{
                    width: '62px',
                    height: '62px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--accent)',
                    color: '#141210',
                    border: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    boxShadow: '0 8px 24px rgba(212, 255, 63, 0.35)',
                    transition: 'transform 0.1s ease',
                    flexShrink: 0,
                  }}
                  onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.92)')}
                  onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
                >
                  {isBuffering ? (
                    <Loader2 size={26} className="tmusic-spin" />
                  ) : isPlaying ? (
                    <Pause size={28} fill="#141210" />
                  ) : (
                    <Play size={28} fill="#141210" style={{ marginLeft: '4px' }} />
                  )}
                </button>
                <button
                  onClick={nextTrack}
                  className="tmusic-np-btn"
                  style={{ color: 'var(--ink)' }}
                  aria-label="Следующий трек"
                  title="Следующий"
                >
                  <SkipForward size={26} />
                </button>
                <button
                  onClick={cycleRepeat}
                  className="tmusic-np-btn"
                  style={{ color: repeat !== 'off' ? 'var(--accent)' : 'var(--dim)' }}
                  aria-label="Повтор"
                  title={repeat === 'off' ? 'Повтор выключен' : repeat === 'all' ? 'Повтор очереди' : 'Повтор трека'}
                >
                  {repeat === 'one' ? <Repeat1 size={20} /> : <Repeat size={20} />}
                </button>
              </div>

              {/* Volume — available in every player mode, under the transport */}
              <VolumeControl className="tm-np-volume" />
            </div>

            {/* Right Column: Queue OR Lyrics */}
            <div
              className={`tmusic-nowplaying-queue ${
                sideMode !== 'art' ? 'mobile-active' : 'mobile-hidden'
              }`}
            >
              {showLyrics ? (
                <>
                  <div
                    style={{
                      padding: '16px 20px',
                      borderBottom: '1px solid var(--line)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      color: 'var(--accent)',
                      fontWeight: '700',
                      fontSize: '13px',
                    }}
                  >
                    <FileText size={16} /> ТЕКСТ ПЕСНИ
                    <span className="tm-panel-volume">
                      <VolumeControl />
                    </span>
                  </div>
                  <div
                    ref={lyricsScrollRef}
                    className="tmusic-lyrics-scroll"
                    style={{ flex: 1, overflowY: 'auto', padding: '18px 20px 28px' }}
                  >
                    {lyricsLoading && (
                      <div style={{ color: 'var(--dim)', fontSize: '13px' }}>Загрузка текста...</div>
                    )}
                    {!lyricsLoading && lyricsError && (
                      <div style={{ color: 'var(--danger, #f87171)', fontSize: '13px' }}>
                        {lyricsError}
                      </div>
                    )}
                    {!lyricsLoading && !lyricsError && !lyrics && (
                      <div style={{ color: 'var(--dim)', fontSize: '13px' }}>
                        Текст не найден для этого трека
                      </div>
                    )}
                    {!lyricsLoading &&
                      displayLines.map((line, idx) => {
                        const isActive = lrcLines.length > 0 && idx === activeLine
                        return (
                          <div
                            key={`${idx}-${line.slice(0, 24)}`}
                            data-lrc={lrcLines.length ? idx : undefined}
                            onClick={() => {
                              if (lrcLines.length && lrcLines[idx]) {
                                seek(lrcLines[idx].time)
                              }
                            }}
                            style={{
                              fontSize: isActive ? '17px' : '14.5px',
                              lineHeight: 1.55,
                              fontWeight: isActive ? 700 : 500,
                              color: isActive ? 'var(--accent)' : 'var(--dim)',
                              marginBottom: '8px',
                              cursor: lrcLines.length ? 'pointer' : 'default',
                              transition: 'color 0.15s ease, font-size 0.15s ease',
                              textShadow: isActive ? '0 0 24px rgba(61,214,198,0.25)' : 'none',
                            }}
                          >
                            {line || '·'}
                          </div>
                        )
                      })}
                  </div>
                </>
              ) : (
                <>
                  <div
                    style={{
                      padding: '14px 20px',
                      borderBottom: '1px solid var(--line)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      color: 'var(--accent)',
                      fontWeight: '700',
                      fontSize: '13px',
                    }}
                  >
                    <ListMusic size={16} /> ОЧЕРЕДЬ ({queue.length})
                    <span className="tm-panel-volume">
                      <VolumeControl />
                    </span>
                    {queue.length > queueIndex + 1 && (
                      <button
                        onClick={clearUpcoming}
                        title="Убрать все треки после текущего"
                        aria-label="Очистить очередь после текущего трека"
                        className="tm-queue-clear"
                      >
                        <ListEnd size={13} /> Очистить
                      </button>
                    )}
                  </div>
                  <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }}>
                    {rows.map(({ t: track, i: idx }, viewIdx) => {
                      const isCurrent = idx === queueIndex
                      const isPast = !shuffle && idx < queueIndex
                      const dur = durations[track.id] || track.duration
                      return (
                        <div
                          key={track.id || idx}
                          className="tm-queue-row"
                          onClick={() => playQueue(queue, idx, currentRelease)}
                          draggable={!isCurrent}
                          onDragStart={(e) => onRowDragStart(e, idx)}
                          onDragOver={onRowDragOver}
                          onDragEnd={onRowDragEnd}
                          onDrop={(e) => e.preventDefault()}
                          style={{
                            padding: '10px 12px',
                            borderRadius: '8px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            backgroundColor: isCurrent ? 'var(--accent-muted)' : 'transparent',
                            cursor: 'pointer',
                            marginBottom: '2px',
                            opacity: isPast ? 0.55 : 1,
                          }}
                        >
                          <span
                            style={{
                              fontSize: '11px',
                              color: isCurrent ? 'var(--accent)' : 'var(--dim)',
                              width: '20px',
                              flexShrink: 0,
                              display: 'inline-flex',
                              justifyContent: 'center',
                            }}
                          >
                            {isCurrent && isPlaying ? <EqBars /> : idx + 1}
                          </span>
                          <span
                            style={{
                              flex: 1,
                              minWidth: 0,
                              fontSize: '13.5px',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              color: isCurrent ? 'var(--accent)' : 'var(--ink)',
                              fontWeight: isCurrent ? 700 : 500,
                            }}
                          >
                            {(track.name || '').replace(/\.[^/.]+$/, '')}
                          </span>
                          {dur && !isCurrent && (
                            <span style={{ fontSize: '11px', color: 'var(--dim)', flexShrink: 0 }}>
                              {formatTime(dur)}
                            </span>
                          )}
                          {!isCurrent && (
                            <span className="tm-queue-actions" onClick={(e) => e.stopPropagation()}>
                              {idx > queueIndex && viewIdx > 0 && (
                                <button
                                  className="tm-queue-mini"
                                  onClick={() => moveQueueItem(idx, idx - 1)}
                                  aria-label="Выше в очереди"
                                  title="Выше"
                                >
                                  <ArrowUp size={12} />
                                </button>
                              )}
                              {idx > queueIndex && idx < queue.length - 1 && (
                                <button
                                  className="tm-queue-mini"
                                  onClick={() => moveQueueItem(idx, idx + 1)}
                                  aria-label="Ниже в очереди"
                                  title="Ниже"
                                >
                                  <ArrowDown size={12} />
                                </button>
                              )}
                              <button
                                className="tm-queue-mini tm-queue-mini-danger"
                                onClick={() => removeFromQueue(idx)}
                                aria-label="Убрать из очереди"
                                title="Убрать"
                              >
                                <Trash2 size={12} />
                              </button>
                            </span>
                          )}
                          {isCurrent && isPlaying && !shuffle && (
                            <span style={{ fontSize: '10px', color: 'var(--accent)', fontWeight: 700, flexShrink: 0, letterSpacing: '0.06em' }}>
                              ИГРАЕТ
                            </span>
                          )}
                        </div>
                      )
                    })}
                    {queue.length === 0 && (
                      <div style={{ padding: '24px 12px', color: 'var(--dim)', fontSize: '13px', textAlign: 'center' }}>
                        Очередь пуста
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ─── Playback notice (stream errors, skips) ─────────────────────────── */}
      {notice && (
        <div
          className={`tmusic-toast ${notice.kind === 'error' ? 'tmusic-toast-error' : ''}`}
          role="alert"
        >
          {notice.kind === 'error' ? <X size={14} style={{ flexShrink: 0 }} /> : <Play size={13} style={{ flexShrink: 0 }} />}
          <span>{notice.text}</span>
          <button
            onClick={dismissNotice}
            className="tmusic-toast-close"
            aria-label="Закрыть уведомление"
          >
            <X size={13} />
          </button>
        </div>
      )}
    </>
  )
}
