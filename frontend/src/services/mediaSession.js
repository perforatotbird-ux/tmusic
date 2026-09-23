/**
 * T-Music Media Session API Bridge
 * Enables system lockscreen controls and track info on iOS, Android, macOS, and Windows.
 * Also bridges to native Android layer when running inside the T-Music APK shell.
 */

import { DEFAULT_COVER } from '../utils/cover'
import { getNativeBridge } from './nativeBridge'

export function setMediaPlaybackState(state) {
  // Bridge to native Android service if present
  try {
    const bridge = getNativeBridge()
    if (bridge?.setPlaybackState) {
      bridge.setPlaybackState(state)
    }
  } catch (err) {
    console.debug('Native bridge setPlaybackState error:', err)
  }

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
  // Expose command dispatcher for native Android wrapper
  if (typeof window !== 'undefined') {
    window.__tmusicNativeCmd = (action, details) => {
      const s = playerStore.getState()
      switch (action) {
        case 'play': {
          const audio = s.audioElement
          if (audio && s.currentTrack && audio.paused) {
            const p = audio.play()
            if (p && p.catch) p.catch(() => {})
            playerStore.setState({ isPlaying: true })
            setMediaPlaybackState('playing')
            return
          }
          if (!s.isPlaying) s.togglePlay()
          break
        }
        case 'pause': {
          if (s.isPlaying) s.togglePlay()
          else if (s.audioElement && !s.audioElement.paused) {
            s.audioElement.pause()
            setMediaPlaybackState('paused')
          }
          break
        }
        case 'playpause': {
          s.togglePlay()
          break
        }
        case 'next': {
          s.nextTrack()
          break
        }
        case 'previous': {
          s.prevTrack()
          break
        }
        case 'seekto': {
          const time = typeof details === 'number' ? details : details?.seekTime
          if (time !== undefined && !isNaN(time)) {
            s.seek(time)
          }
          break
        }
        default:
          console.warn('Unknown native media command:', action)
      }
    }
  }

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
  if (!track) return

  const title = (track.name || 'Без названия').replace(/\.[^/.]+$/, '')
  const artist = release?.artistName || 'T-Music'
  const album = release?.title || ''
  const coverUrl = release?.coverUrl || DEFAULT_COVER
  const duration = track.duration || 0

  // Bridge to native Android service
  try {
    const bridge = getNativeBridge()
    if (bridge?.updateMetadata) {
      bridge.updateMetadata(title, artist, album, coverUrl, duration)
    }
  } catch (err) {
    console.debug('Native bridge updateMetadata error:', err)
  }

  if (!('mediaSession' in navigator)) return

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
  if (!duration || isNaN(duration)) return

  const safeDuration = Math.max(0, duration)
  const safePosition = Math.max(0, Math.min(position, duration))

  // Bridge to native Android service
  try {
    const bridge = getNativeBridge()
    if (bridge?.updatePosition) {
      bridge.updatePosition(safeDuration, safePosition)
    }
  } catch (err) {
    console.debug('Native bridge updatePosition error:', err)
  }

  if (!('mediaSession' in navigator)) return
  try {
    navigator.mediaSession.setPositionState({
      duration: safeDuration,
      playbackRate: 1,
      position: safePosition,
    })
  } catch {
    // Ignore position state errors on unsupported browsers
  }
}
