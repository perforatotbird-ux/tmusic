import React from 'react'
import { usePlayerStore } from '../store/playerStore'
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
  Trash2,
} from 'lucide-react'

function formatTime(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

export function MusicLibraryPlayer() {
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
  const coverUrl = currentRelease?.coverUrl || '/icon.svg'

  return (
    <>
      {/* ─── Compact Bottom Player Bar ────────────────────────────────────────── */}
      <div
        style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          height: '74px',
          backgroundColor: 'var(--bg-card)',
          borderTop: '1px solid var(--line-bright)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 20px',
          zIndex: 8000,
          boxShadow: '0 -10px 30px rgba(0,0,0,0.5)',
        }}
      >
        {/* Track Info */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px', width: '28%', minWidth: '180px' }}>
          <img
            src={coverUrl}
            alt="Cover"
            onClick={() => setPlayerExpanded(true)}
            style={{
              width: '46px',
              height: '46px',
              borderRadius: '6px',
              objectFit: 'cover',
              cursor: 'pointer',
              border: '1px solid var(--line)',
            }}
          />
          <div style={{ overflow: 'hidden' }}>
            <div
              onClick={() => setPlayerExpanded(true)}
              style={{
                color: 'var(--ink)',
                fontWeight: '600',
                fontSize: '13.5px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                cursor: 'pointer',
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

        {/* Center Controls & Scrubber */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '6px',
            flex: 1,
            maxWidth: '560px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <button
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
              onClick={togglePlay}
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

        {/* Right Tools (Volume & Expand) */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '14px',
            width: '28%',
            minWidth: '160px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
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
          style={{
            position: 'fixed',
            inset: 0,
            backgroundColor: 'var(--bg)',
            zIndex: 9500,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* Top Bar */}
          <div
            style={{
              height: '60px',
              padding: '0 24px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              borderBottom: '1px solid var(--line)',
            }}
          >
            <div className="title-zine" style={{ fontSize: '15px', color: 'var(--accent)', letterSpacing: '0.05em' }}>
              NOW PLAYING
            </div>
            <button
              onClick={() => setPlayerExpanded(false)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--dim)',
                cursor: 'pointer',
              }}
            >
              <Minimize2 size={20} />
            </button>
          </div>

          {/* Main Content Grid */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'row',
              overflow: 'hidden',
              padding: '30px',
              gap: '40px',
            }}
          >
            {/* Left Column: Artwork & Track Meta */}
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                maxWidth: '520px',
                margin: '0 auto',
              }}
            >
              <img
                src={coverUrl}
                alt="Cover Full"
                style={{
                  width: '100%',
                  maxWidth: '380px',
                  aspectRatio: '1/1',
                  borderRadius: '16px',
                  objectFit: 'cover',
                  boxShadow: '0 25px 50px rgba(0,0,0,0.7)',
                  border: '1px solid var(--line-bright)',
                  marginBottom: '28px',
                }}
              />
              <h2
                style={{
                  fontSize: '22px',
                  fontWeight: '700',
                  color: 'var(--ink)',
                  textAlign: 'center',
                  marginBottom: '6px',
                }}
              >
                {trackTitle}
              </h2>
              <div style={{ fontSize: '15px', color: 'var(--accent)', marginBottom: '4px' }}>
                {artistName}
              </div>
              <div style={{ fontSize: '13px', color: 'var(--dim)', marginBottom: '24px' }}>
                {albumTitle}
              </div>

              {/* Progress Slider */}
              <div style={{ width: '100%', marginBottom: '20px' }}>
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
              <div style={{ display: 'flex', alignItems: 'center', gap: '28px' }}>
                <button
                  onClick={toggleShuffle}
                  style={{ background: 'none', border: 'none', color: shuffle ? 'var(--accent)' : 'var(--dim)', cursor: 'pointer' }}
                >
                  <Shuffle size={20} />
                </button>
                <button
                  onClick={prevTrack}
                  style={{ background: 'none', border: 'none', color: 'var(--ink)', cursor: 'pointer' }}
                >
                  <SkipBack size={26} />
                </button>
                <button
                  onClick={togglePlay}
                  style={{
                    width: '60px',
                    height: '60px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--accent)',
                    color: '#141210',
                    border: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                  }}
                >
                  {isPlaying ? <Pause size={28} fill="#141210" /> : <Play size={28} fill="#141210" style={{ marginLeft: '4px' }} />}
                </button>
                <button
                  onClick={nextTrack}
                  style={{ background: 'none', border: 'none', color: 'var(--ink)', cursor: 'pointer' }}
                >
                  <SkipForward size={26} />
                </button>
                <button
                  onClick={cycleRepeat}
                  style={{ background: 'none', border: 'none', color: repeat !== 'off' ? 'var(--accent)' : 'var(--dim)', cursor: 'pointer' }}
                >
                  {repeat === 'one' ? <Repeat1 size={20} /> : <Repeat size={20} />}
                </button>
              </div>
            </div>

            {/* Right Column: Active Queue */}
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                backgroundColor: 'var(--bg-card)',
                borderRadius: 'var(--radius)',
                border: '1px solid var(--line)',
                overflow: 'hidden',
                maxHeight: '100%',
              }}
            >
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
                        padding: '10px 14px',
                        borderRadius: '6px',
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
                        <span style={{ fontSize: '11px', color: 'var(--dim)', width: '18px' }}>
                          {idx + 1}
                        </span>
                        <span style={{ fontSize: '13px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {t.name.replace(/\.[^/.]+$/, '')}
                        </span>
                      </div>
                      {isCurrent && isPlaying && (
                        <div style={{ fontSize: '11px', color: 'var(--accent)', fontWeight: '700' }}>
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
