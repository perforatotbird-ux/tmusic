import React, { useState } from 'react'
import { Play, Pause, Star, HardDrive, Music, ListPlus } from 'lucide-react'
import { usePlayerStore } from '../../store/playerStore'
import { useFavoritesStore } from '../../store/favoritesStore'
import { useOfflineStore } from '../../store/offlineStore'
import { useAuthStore } from '../../store/authStore'

function formatTime(sec) {
  if (!sec || isNaN(sec)) return ''
  const s = Math.max(0, Math.floor(sec))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function SearchTrackItem({ track, onPlay, onOpenArtist, onOpenAlbum }) {
  const [hovered, setHovered] = useState(false)
  const [imgError, setImgError] = useState(false)

  const currentTrack = usePlayerStore((s) => s.currentTrack)
  const isPlaying = usePlayerStore((s) => s.isPlaying)
  const togglePlay = usePlayerStore((s) => s.togglePlay)
  const addToQueue = usePlayerStore((s) => s.addToQueue)

  const isCurrent = currentTrack?.id === track.id
  const isTrackPlaying = isCurrent && isPlaying

  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const favIds = useFavoritesStore((s) => s.ids)
  const toggleFavorite = useFavoritesStore((s) => s.toggle)
  const isFav = isAuthenticated && favIds.has(Number(track.id))

  const cachedTrackIds = useOfflineStore((s) => s.cachedTrackIds)
  const isCached = cachedTrackIds.has(Number(track.id))

  const handlePlayClick = (e) => {
    e.stopPropagation()
    if (isCurrent) {
      togglePlay()
    } else {
      onPlay?.(track)
    }
  }

  const handleFavClick = (e) => {
    e.stopPropagation()
    toggleFavorite(track.id)
  }

  const handleQueueClick = (e) => {
    e.stopPropagation()
    addToQueue(track)
    // UI-40: mirror the feedback other "add to queue" actions give the user.
    usePlayerStore.getState().pushNotice({
      kind: 'info',
      text: `«${track.title || 'Трек'}» добавлен в очередь`,
    })
  }

  const coverSrc = track.custom_image_url || track.cover_url

  return (
    <div
      className="tm-search-track-row"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={handlePlayClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        padding: '8px 12px',
        borderRadius: '10px',
        gap: '12px',
        cursor: 'pointer',
        transition: 'background-color 0.15s ease',
        backgroundColor: isCurrent
          ? 'rgba(212, 255, 63, 0.08)'
          : hovered
          ? 'rgba(255, 255, 255, 0.04)'
          : 'transparent',
      }}
    >
      {/* Thumbnail + Play Overlay */}
      <div
        style={{
          position: 'relative',
          width: '42px',
          height: '42px',
          borderRadius: '8px',
          overflow: 'hidden',
          flexShrink: 0,
          backgroundColor: 'rgba(255, 255, 255, 0.05)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {coverSrc && !imgError ? (
          <img
            src={coverSrc}
            alt={track.title}
            onError={() => setImgError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : (
          <Music size={18} style={{ color: 'var(--dim)' }} />
        )}

        {/* Play/Pause icon button */}
        {(hovered || isCurrent) && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              backgroundColor: 'rgba(0, 0, 0, 0.55)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: isCurrent ? 'var(--accent)' : '#fff',
            }}
          >
            {isTrackPlaying ? <Pause size={18} /> : <Play size={18} style={{ marginLeft: '2px' }} />}
          </div>
        )}
      </div>

      {/* Track Info */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '3px' }}>
        <div
          style={{
            fontSize: '14px',
            fontWeight: 600,
            color: isCurrent ? 'var(--accent)' : 'var(--ink)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
          title={track.title}
        >
          {track.title}
        </div>
        <div
          style={{
            fontSize: '12px',
            color: 'var(--dim)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: 'flex',
            alignItems: 'center',
            gap: '5px',
          }}
        >
          {track.artist_name && (
            <span
              onClick={(e) => {
                if (onOpenArtist && track.artist_id) {
                  e.stopPropagation()
                  onOpenArtist(track.artist_id)
                }
              }}
              style={{
                cursor: onOpenArtist && track.artist_id ? 'pointer' : 'inherit',
                textDecoration: onOpenArtist && track.artist_id && hovered ? 'underline' : 'none',
              }}
            >
              {track.artist_name}
            </span>
          )}
          {track.artist_name && track.album_title && <span>•</span>}
          {track.album_title && (
            <span
              onClick={(e) => {
                if (onOpenAlbum && track.album_id) {
                  e.stopPropagation()
                  onOpenAlbum({ id: track.album_id, title: track.album_title, folder_id: track.folder_id })
                }
              }}
              style={{
                cursor: onOpenAlbum && track.album_id ? 'pointer' : 'inherit',
                textDecoration: onOpenAlbum && track.album_id && hovered ? 'underline' : 'none',
              }}
            >
              {track.album_title}
            </span>
          )}
        </div>
      </div>

      {/* Offline cached badge */}
      {isCached && (
        <span
          title="В офлайн-кэше"
          style={{
            color: 'var(--accent)',
            display: 'flex',
            alignItems: 'center',
            flexShrink: 0,
          }}
        >
          <HardDrive size={14} />
        </span>
      )}

      {/* Favorite Button */}
      {isAuthenticated && (
        <button
          type="button"
          className="tm-search-action-btn"
          data-active={isFav}
          onClick={handleFavClick}
          aria-label={isFav ? 'Удалить из избранного' : 'Добавить в избранное'}
          aria-pressed={isFav}
        >
          <Star size={16} fill={isFav ? 'var(--accent)' : 'none'} />
        </button>
      )}

      {/* Add to queue button — always in the DOM so it is reachable on touch. */}
      <button
        type="button"
        className="tm-search-action-btn"
        onClick={handleQueueClick}
        title="Добавить в очередь"
        aria-label="Добавить в очередь"
      >
        <ListPlus size={16} />
      </button>

      {/* Duration */}
      {track.duration && (
        <span
          style={{
            fontSize: '12px',
            color: 'var(--dim)',
            minWidth: '36px',
            textAlign: 'right',
            flexShrink: 0,
          }}
        >
          {formatTime(track.duration)}
        </span>
      )}
    </div>
  )
}
