/**
 * Album detail sheet: tracklist + playback entry point.
 *
 * Opens when the user taps an album cover.
 * Desktop: centered modal. Phone: bottom sheet (both driven by CSS classes).
 */

import React, { useEffect, useRef, useState } from 'react'
import { streamApi } from '../api/stream'
import { resolveCoverUrl } from '../utils/cover'
import { usePlayerStore } from '../store/playerStore'
import {
  X,
  Play,
  Pause,
  Loader2,
  Music,
  ListMusic,
  Image as ImageIcon,
} from 'lucide-react'

function formatSizeHint(track) {
  if (track?.size && Number.isFinite(track.size) && track.size > 0) {
    const mb = track.size / (1024 * 1024)
    return mb >= 1 ? `${mb.toFixed(1)} МБ` : `${Math.max(1, Math.round(track.size / 1024))} КБ`
  }
  return ''
}

function stripExt(name) {
  return (name || 'Без названия').replace(/\.[^/.]+$/, '')
}

export function AlbumDetail({ album, artist = null, onClose, onOpenCoverPicker }) {
  const [tracks, setTracks] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const loadSeq = useRef(0)

  const { playQueue, queue, queueIndex, isPlaying, togglePlay } = usePlayerStore()

  const cover = resolveCoverUrl(album, artist, null)
  const albumTitle = album?.title || album?.display_name || 'Альбом'
  const artistName = (artist && artist.display_name) || albumTitle

  // Lock body scroll while the sheet is open.
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [])

  // Escape closes the sheet on desktop keyboards.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Load tracklist; ignore late responses from a superseded album.
  useEffect(() => {
    let cancelled = false
    const mySeq = ++loadSeq.current
    setIsLoading(true)
    setError(null)
    setTracks([])
    ;(async () => {
      try {
        const list = await streamApi.getFolderTracks(album.folder_id, !album.is_tracks)
        if (!cancelled && mySeq === loadSeq.current) {
          setTracks(Array.isArray(list) ? list : [])
        }
      } catch (e) {
        if (!cancelled && mySeq === loadSeq.current) {
          setError('Не удалось загрузить треки')
        }
      } finally {
        if (!cancelled && mySeq === loadSeq.current) {
          setIsLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [album?.folder_id, album?.is_tracks])

  const releaseInfo = {
    title: albumTitle,
    artistName,
    coverUrl: resolveCoverUrl(album, artist),
  }

  const handlePlayAll = () => {
    if (tracks.length > 0) playQueue(tracks, 0, releaseInfo)
  }

  const handlePlayTrack = (idx) => {
    playQueue(tracks, idx, releaseInfo)
  }

  const isThisQueue = queue.length > 0 && tracks.length > 0 && queue[0]?.id === tracks[0]?.id

  return (
    <div
      className="tmusic-album-overlay"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={albumTitle}
    >
      <div className="tmusic-album-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="tmusic-sheet-handle" />
        <div className="tmusic-album-head">
          <div style={{ position: 'relative', flexShrink: 0 }}>
            {cover ? (
              <img
                src={cover}
                alt={albumTitle}
                style={{
                  width: '84px',
                  height: '84px',
                  borderRadius: '10px',
                  objectFit: 'cover',
                  border: '1px solid var(--line-bright)',
                  display: 'block',
                }}
              />
            ) : (
              <div
                style={{
                  width: '84px',
                  height: '84px',
                  borderRadius: '10px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--line)',
                  color: 'var(--dim)',
                }}
              >
                <Music size={28} />
              </div>
            )}
            {onOpenCoverPicker && (
              <button
                onClick={() =>
                  onOpenCoverPicker({
                    type: album.title ? 'album' : 'artist',
                    id: album.id,
                    title: albumTitle,
                    hasCustom: !!album.custom_image_url,
                  })
                }
                title="Сменить обложку"
                style={{
                  position: 'absolute',
                  bottom: '-6px',
                  right: '-6px',
                  width: '28px',
                  height: '28px',
                  borderRadius: '50%',
                  backgroundColor: 'var(--bg-card)',
                  border: '1px solid var(--line-bright)',
                  color: 'var(--ink)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: 'pointer',
                }}
              >
                <ImageIcon size={13} />
              </button>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: '11px',
                textTransform: 'uppercase',
                color: 'var(--accent)',
                fontWeight: '700',
                letterSpacing: '0.08em',
                marginBottom: '2px',
              }}
            >
              {album?.year || 'Релиз'}
            </div>
            <h2
              className="title-zine"
              style={{
                fontSize: '19px',
                lineHeight: 1.2,
                margin: '0 0 2px 0',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {albumTitle}
            </h2>
            <div style={{ fontSize: '12.5px', color: 'var(--dim)' }}>
              {artistName}
              {!isLoading && tracks.length > 0 && ` • ${tracks.length} тр.`}
            </div>
            <button
              onClick={handlePlayAll}
              disabled={isLoading || tracks.length === 0}
              style={{
                marginTop: '10px',
                padding: '9px 18px',
                backgroundColor: tracks.length === 0 ? 'var(--line-bright)' : 'var(--accent)',
                color: '#141210',
                border: 'none',
                borderRadius: '999px',
                fontWeight: '800',
                fontSize: '12.5px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                cursor: tracks.length === 0 ? 'default' : 'pointer',
                opacity: isLoading ? 0.6 : 1,
                minHeight: '40px',
              }}
            >
              {isLoading ? <Loader2 size={15} className="tmusic-spin" /> : <Play size={15} fill="#141210" />}
              Слушать всё
            </button>
          </div>

          <button
            onClick={onClose}
            aria-label="Закрыть"
            style={{
              alignSelf: 'flex-start',
              background: 'none',
              border: 'none',
              color: 'var(--dim)',
              cursor: 'pointer',
              padding: '8px',
              minWidth: '40px',
              minHeight: '40px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <X size={20} />
          </button>

        </div>

        <div className="tmusic-album-list">
          {isLoading && (
            <div className="tmusic-album-state">
              <Loader2 size={18} className="tmusic-spin" />
              Загрузка треков…
            </div>
          )}

          {!isLoading && error && (
            <div className="tmusic-album-state" style={{ color: 'var(--danger)' }}>
              {error}
            </div>
          )}

          {!isLoading && !error && tracks.length === 0 && (
            <div className="tmusic-album-state">В этом релизе нет треков</div>
          )}

          {!isLoading &&
            !error &&
            tracks.map((t, idx) => {
              const isCurrent = isThisQueue && queue[queueIndex]?.id === t.id
              const playing = isCurrent && isPlaying
              return (
                <div
                  key={t.id || idx}
                  onClick={() => (isCurrent ? togglePlay() : handlePlayTrack(idx))}
                  className="tmusic-track-row"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '10px 12px',
                    borderRadius: '10px',
                    cursor: 'pointer',
                    backgroundColor: isCurrent ? 'var(--accent-muted)' : 'transparent',
                  }}
                >
                  <span
                    style={{
                      width: '26px',
                      flexShrink: 0,
                      textAlign: 'center',
                      color: isCurrent ? 'var(--accent)' : 'var(--dim)',
                      fontSize: '12px',
                      fontWeight: isCurrent ? '800' : '400',
                    }}
                  >
                    {playing ? (
                      <Pause size={15} fill="var(--accent)" style={{ verticalAlign: 'middle' }} />
                    ) : isCurrent ? (
                      <Play size={15} fill="var(--accent)" style={{ verticalAlign: 'middle' }} />
                    ) : (
                      idx + 1
                    )}
                  </span>
                  <div
                    style={{
                      flex: 1,
                      minWidth: 0,
                      fontSize: '13.5px',
                      fontWeight: isCurrent ? '700' : '500',
                      color: isCurrent ? 'var(--accent)' : 'var(--ink)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {stripExt(t.name)}
                  </div>
                  {formatSizeHint(t) && (
                    <span style={{ fontSize: '11px', color: 'var(--dim)', flexShrink: 0 }}>
                      {formatSizeHint(t)}
                    </span>
                  )}
                </div>
              )
            })}
        </div>

        {!isLoading && tracks.length > 0 && (
          <div className="tmusic-album-foot">
            <ListMusic size={14} />
            {tracks.length} {tracks.length === 1 ? 'трек' : 'треков'} • тап по строке — играть / пауза
          </div>
        )}
      </div>
    </div>
  )
}
