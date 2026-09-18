import React, { useState } from 'react'
import { usePlayerStore } from '../store/playerStore'
import { resolveReleaseCoverUrl } from '../utils/cover'
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
  Minimize2,
  ListMusic,
  ChevronDown,
  Trash2,
} from 'lucide-react'

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

export function MusicLibraryPlayer() {
  const [showQueue, setShowQueue] = useState(false)
  const {
    currentTrack,
    currentRelease,
    queue,
    queueIndex,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    shuffle,
    repeat,
    isPlayerExpanded,
    togglePlay,
    forceResume,
    nextTrack,
    prevTrack,
    seek,
    setVolume,
    toggleMute,
    toggleShuffle,
    cycleRepeat,
    setPlayerExpanded,
    playQueue,
  } = usePlayerStore()

  if (!currentTrack) return null

  const trackTitle = (currentTrack.name || 'Без названия').replace(/\.[^/.]+$/, '')
  const artistName = currentRelease?.artistName || 'T-Music'
  const albumTitle = currentRelease?.title || ''
  const coverUrl = resolveReleaseCoverUrl(currentRelease)

  // Android: store may say "playing" while <audio> is paused after lockscreen.
  const handlePlayPause = () => {
    const audio = usePlayerStore.getState().audioElement
    if (isPlaying && audio?.paused) {
      forceResume()
      return
    }
    togglePlay()
  }

  return (
    <>
      {/* ─── Compact Bottom Player Bar ────────────────────────────────────────── */}
      <div
        className="tmusic-playerbar"
        onClick={(e) => {
          if (!e.target.closest('button') && !e.target.closest('input')) {
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
              flexShrink: 0,
            }}
          />
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
            {isPlaying ? <Pause size={20} fill="#141210" /> : <Play size={20} fill="#141210" style={{ marginLeft: '2px' }} />}
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
              style={{
                background: 'none',
                border: 'none',
                color: shuffle ? 'var(--accent)' : 'var(--dim)',
                cursor: 'pointer',
              }}
            >
              <Shuffle size={15} />
            </button>
            <button
              onClick={prevTrack}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--ink)',
                cursor: 'pointer',
              }}
            >
              <SkipBack size={18} />
            </button>
            <button
              onClick={handlePlayPause}
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
              }}
              onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.92)')}
              onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
            >
              {isPlaying ? <Pause size={18} fill="#141210" /> : <Play size={18} fill="#141210" style={{ marginLeft: '2px' }} />}
            </button>
            <button
              onClick={nextTrack}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--ink)',
                cursor: 'pointer',
              }}
            >
              <SkipForward size={18} />
            </button>
            <button
              className="tmusic-hide-mobile"
              onClick={cycleRepeat}
              style={{
                background: 'none',
                border: 'none',
                color: repeat !== 'off' ? 'var(--accent)' : 'var(--dim)',
                cursor: 'pointer',
              }}
            >
              {repeat === 'one' ? <Repeat1 size={15} /> : <Repeat size={15} />}
            </button>
          </div>

          {/* Scrubber */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', width: '100%' }}>
            <span style={{ fontSize: '11px', color: 'var(--dim)', width: '32px', textAlign: 'right' }}>
              {formatTime(currentTime)}
            </span>
            <input
              type="range"
              min="0"
              max={duration || 100}
              value={currentTime || 0}
              onChange={(e) => seek(parseFloat(e.target.value))}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: '11px', color: 'var(--dim)', width: '32px' }}>
              {formatTime(duration)}
            </span>
          </div>
        </div>

        {/* Right Tools (Volume & Expand - Desktop) */}
        <div className="tmusic-player-tools">
          <div className="tmusic-player-volume">
            <button
              onClick={toggleMute}
              style={{ background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer' }}
            >
              {isMuted || volume === 0 ? <VolumeX size={17} /> : <Volume2 size={17} />}
            </button>
            <input
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={isMuted ? 0 : volume}
              onChange={(e) => setVolume(parseFloat(e.target.value))}
              style={{ width: '70px' }}
            />
          </div>
          <button
            onClick={() => setPlayerExpanded(true)}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--dim)',
              cursor: 'pointer',
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
          }}
        >
          {/* Top Bar */}
          <div
            className="tmusic-nowplaying-topbar"
            style={{
              borderBottom: '1px solid var(--line)',
            }}
          >
            <button
              onClick={() => setPlayerExpanded(false)}
              className="tmusic-np-btn"
              aria-label="Свернуть"
              title="Свернуть"
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
              onClick={() => setShowQueue((prev) => !prev)}
              className={`tmusic-np-btn ${showQueue ? 'active' : ''}`}
              aria-label="Очередь воспроизведения"
              title={showQueue ? 'Показать обложку' : 'Показать очередь'}
            >
              <ListMusic size={22} />
            </button>
          </div>

          {/* Main Content Grid */}
          <div className="tmusic-nowplaying-body">
            {/* Left Column: Artwork & Track Meta */}
            <div className={`tmusic-nowplaying-art ${showQueue ? 'mobile-hidden' : ''}`}>
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
                  marginBottom: '20px',
                }}
              />
              <h2
                style={{
                  fontSize: '20px',
                  fontWeight: '700',
                  color: 'var(--ink)',
                  textAlign: 'center',
                  marginBottom: '4px',
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

              {/* Progress Slider */}
              <div style={{ width: '100%', maxWidth: '380px', marginBottom: '16px' }}>
                <input
                  type="range"
                  min="0"
                  max={duration || 100}
                  value={currentTime || 0}
                  onChange={(e) => seek(parseFloat(e.target.value))}
                  style={{ width: '100%', height: '6px' }}
                />
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
                  title="Перемешать"
                >
                  <Shuffle size={20} />
                </button>
                <button
                  onClick={prevTrack}
                  className="tmusic-np-btn"
                  style={{ color: 'var(--ink)' }}
                  title="Предыдущий"
                >
                  <SkipBack size={26} />
                </button>
                <button
                  onClick={handlePlayPause}
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
                  }}
                  onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.92)')}
                  onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
                >
                  {isPlaying ? <Pause size={28} fill="#141210" /> : <Play size={28} fill="#141210" style={{ marginLeft: '4px' }} />}
                </button>
                <button
                  onClick={nextTrack}
                  className="tmusic-np-btn"
                  style={{ color: 'var(--ink)' }}
                  title="Следующий"
                >
                  <SkipForward size={26} />
                </button>
                <button
                  onClick={cycleRepeat}
                  className="tmusic-np-btn"
                  style={{ color: repeat !== 'off' ? 'var(--accent)' : 'var(--dim)' }}
                  title="Повтор"
                >
                  {repeat === 'one' ? <Repeat1 size={20} /> : <Repeat size={20} />}
                </button>
              </div>
            </div>

            {/* Right Column: Active Queue */}
            <div className={`tmusic-nowplaying-queue ${showQueue ? 'mobile-active' : 'mobile-hidden'}`}>
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
                <ListMusic size={16} /> ОЧЕРЕДЬ ВОСПРОИЗВЕДЕНИЯ ({queue.length})
              </div>
              <div style={{ flex: 1, overflowY: 'auto', padding: '10px' }}>
                {queue.map((t, idx) => {
                  const isCurrent = idx === queueIndex
                  return (
                    <div
                      key={t.id || idx}
                      onClick={() => playQueue(queue, idx, currentRelease)}
                      style={{
                        padding: '12px 14px',
                        borderRadius: '8px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        backgroundColor: isCurrent ? 'var(--accent-muted)' : 'transparent',
                        color: isCurrent ? 'var(--accent)' : 'var(--ink)',
                        cursor: 'pointer',
                        marginBottom: '4px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', overflow: 'hidden' }}>
                        <span style={{ fontSize: '11px', color: 'var(--dim)', width: '20px' }}>
                          {idx + 1}
                        </span>
                        <span style={{ fontSize: '13.5px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {t.name.replace(/\.[^/.]+$/, '')}
                        </span>
                      </div>
                      {isCurrent && isPlaying && (
                        <div style={{ fontSize: '11px', color: 'var(--accent)', fontWeight: '700', flexShrink: 0 }}>
                          ИГРАЕТ
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
