/**
 * T-Music Media Session API Bridge
 * Enables system lockscreen controls and track info on iOS, Android, macOS, and Windows.
 */

import { DEFAULT_COVER } from '../utils/cover'

export function setMediaPlaybackState(state) {
  if (!('mediaSession' in navigator)) return
  try {
    // 'playing' | 'paused' | 'none'
    // Android uses this to keep/tear down the audio focus + lockscreen UI.
    navigator.mediaSession.playbackState = state
  } catch {
    /* ignore */
  }
}

export function setupMediaSession(playerStore) {
  if (!('mediaSession' in navigator)) return

  const ms = navigator.mediaSession

  ms.setActionHandler('play', () => {
    const s = playerStore.getState()
    // OS "play" after lockscreen — resume the element even if store says playing
    // (Android often pauses the <audio> while keeping our isPlaying=true).
    const audio = s.audioElement
    if (audio && s.currentTrack && audio.paused) {
      const p = audio.play()
      if (p && p.catch) p.catch(() => {})
      playerStore.setState({ isPlaying: true })
      setMediaPlaybackState('playing')
      return
    }
    if (!s.isPlaying) s.togglePlay()
  })

  ms.setActionHandler('pause', () => {
    const s = playerStore.getState()
    if (s.isPlaying) s.togglePlay()
    else if (s.audioElement && !s.audioElement.paused) {
      s.audioElement.pause()
      setMediaPlaybackState('paused')
    }
  })

  ms.setActionHandler('previoustrack', () => playerStore.getState().prevTrack())
  ms.setActionHandler('nexttrack', () => playerStore.getState().nextTrack())
  ms.setActionHandler('seekto', (details) => {
    if (details.seekTime !== undefined) {
      playerStore.getState().seek(details.seekTime)
    }
  })
}

export function updateMediaSessionMetadata(track, release) {
  if (!('mediaSession' in navigator) || !track) return

  const title = (track.name || 'Без названия').replace(/\.[^/.]+$/, '')
  const artist = release?.artistName || 'T-Music'
  const album = release?.title || ''
  const coverUrl = release?.coverUrl || DEFAULT_COVER

  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title,
      artist,
      album,
      artwork: [
        { src: coverUrl, sizes: '96x96', type: 'image/jpeg' },
        { src: coverUrl, sizes: '128x128', type: 'image/jpeg' },
        { src: coverUrl, sizes: '192x192', type: 'image/jpeg' },
        { src: coverUrl, sizes: '256x256', type: 'image/jpeg' },
        { src: coverUrl, sizes: '512x512', type: 'image/jpeg' },
      ],
    })
    // Keep lockscreen session alive while advancing tracks.
    setMediaPlaybackState('playing')
  } catch {
    /* ignore */
  }
}

export function updateMediaSessionPosition(duration, position) {
  if (!('mediaSession' in navigator) || !duration || isNaN(duration)) return
  try {
    navigator.mediaSession.setPositionState({
      duration: Math.max(0, duration),
      playbackRate: 1,
      position: Math.max(0, Math.min(position, duration)),
    })
  } catch {
    // Ignore position state errors on unsupported browsers
  }
}
