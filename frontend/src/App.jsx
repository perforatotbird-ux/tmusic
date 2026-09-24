import React, { useEffect, useRef, useState } from 'react'
import { useAuthStore } from './store/authStore'
import { usePlayerStore } from './store/playerStore'
import { useFavoritesStore } from './store/favoritesStore'
import { setupMediaSession, updateMediaSessionMetadata, updateMediaSessionPosition } from './services/mediaSession'
import { IS_NATIVE } from './services/nativeBridge'
import {
  onTrackChanged,
  tryRestorePosition,
  onTimeUpdate as playbackOnTimeUpdate,
  onEnded as playbackOnEnded,
} from './services/playbackProgress'
import { AuthModal } from './components/AuthModal'
import { MusicLibrary } from './components/MusicLibrary'
import { MusicLibraryPlayer } from './components/MusicLibraryPlayer'
import { CoverPicker } from './components/CoverPicker'
import { createLogger } from './utils/logger'

const log = createLogger('app')

export function App() {
  const audioRef = useRef(null)
  const [coverTarget, setCoverTarget] = useState(null)
  // Track id we've already bound restore/metadata for (store owns src/play).
  const boundTrackIdRef = useRef(null)

  const { checkAuth, setAuthModalOpen } = useAuthStore()
  const {
    currentTrack,
    currentRelease,
    setAudioElement,
    precacheNextTracks,
  } = usePlayerStore()

  useEffect(() => {
    fetch('/api/system/config')
      .then((res) => (res.ok ? res.json() : null))
      .then((config) => {
        if (config?.site_title) document.title = config.site_title
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    log.info('App mounted, checking session')
    checkAuth()
    setupMediaSession(usePlayerStore)

    const handleUnauthorized = () => {
      log.warn('received tmusic:unauthorized → opening auth modal')
      setAuthModalOpen(true)
    }
    window.addEventListener('tmusic:unauthorized', handleUnauthorized)

    // Android lockscreen / tab throttle: resume if we believe playback continues.
    // In native WebView with ForegroundService, skip this to avoid race conditions.
    let onVis = null
    let resumeIfNeeded = null

    if (!IS_NATIVE) {
      resumeIfNeeded = () => {
        if (document.visibilityState && document.visibilityState !== 'visible') return
        const { isPlaying, currentTrack: track, audioElement } = usePlayerStore.getState()
        if (isPlaying && track && audioElement && audioElement.paused) {
          const p = audioElement.play()
          if (p && p.catch) p.catch(() => {})
        }
      }
      onVis = () => resumeIfNeeded()
      document.addEventListener('visibilitychange', onVis)
      window.addEventListener('focus', resumeIfNeeded)
    }

    return () => {
      log.info('App unmounted')
      window.removeEventListener('tmusic:unauthorized', handleUnauthorized)
      if (onVis) document.removeEventListener('visibilitychange', onVis)
      if (resumeIfNeeded) window.removeEventListener('focus', resumeIfNeeded)
    }
  }, [])

  // Global player hotkeys: Space, ←/→ seek, Shift+←/→ track, M mute, L like.
  useEffect(() => {
    const isTypingTarget = (el) => {
      if (!el) return false
      const tag = el.tagName
      return (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        el.isContentEditable
      )
    }

    const onKeyDown = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (isTypingTarget(e.target)) return
      const s = usePlayerStore.getState()
      if (!s.currentTrack) return

      const key = e.key
      if (key === ' ' || key === 'Spacebar') {
        e.preventDefault()
        const audio = s.audioElement
        if (s.isPlaying && audio?.paused) s.forceResume()
        else s.togglePlay()
        log.debug('hotkey', { key: 'space' })
        return
      }
      if (key === 'ArrowLeft') {
        e.preventDefault()
        if (e.shiftKey) s.prevTrack()
        else {
          const audio = s.audioElement
          if (audio) s.seek(Math.max(0, audio.currentTime - 5))
        }
        log.debug('hotkey', { key: 'arrowleft', shift: e.shiftKey })
        return
      }
      if (key === 'ArrowRight') {
        e.preventDefault()
        if (e.shiftKey) s.nextTrack()
        else {
          const audio = s.audioElement
          if (audio) {
            const dur = Number.isFinite(audio.duration) ? audio.duration : Infinity
            s.seek(Math.min(dur, audio.currentTime + 5))
          }
        }
        return
      }
      if (key === 'ArrowUp') {
        e.preventDefault()
        s.setVolume(Math.min(1, (s.isMuted ? 0 : s.volume) + 0.05))
        return
      }
      if (key === 'ArrowDown') {
        e.preventDefault()
        s.setVolume(Math.max(0, (s.isMuted ? 0 : s.volume) - 0.05))
        return
      }
      if (key === 'm' || key === 'M' || key === 'ь' || key === 'Ь') {
        e.preventDefault()
        s.toggleMute()
        return
      }
      if (key === 'l' || key === 'L' || key === 'д' || key === 'Д') {
        const { isAuthenticated } = useAuthStore.getState()
        if (!isAuthenticated) return
        e.preventDefault()
        useFavoritesStore.getState().toggle(s.currentTrack.id)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    if (audioRef.current) {
      setAudioElement(audioRef.current)
    }
  }, [audioRef.current])

  // Metadata + resume-offset when the active track changes.
  // src/play() are started synchronously in playerStore (Android-safe).
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !currentTrack) return
    if (boundTrackIdRef.current === currentTrack.id) return
    boundTrackIdRef.current = currentTrack.id

    onTrackChanged(currentTrack.id)
    updateMediaSessionMetadata(currentTrack, currentRelease)

    const restoreHandler = () => {
      tryRestorePosition(audio, currentTrack.id)
    }
    audio.addEventListener('loadedmetadata', restoreHandler)

    // If store already set src but browser paused — kick play when track appears
    const state = usePlayerStore.getState()
    if (state.isPlaying && audio.paused) {
      const p = audio.play()
      if (p && p.catch) p.catch(() => {})
    }

    return () => audio.removeEventListener('loadedmetadata', restoreHandler)
  }, [currentTrack?.id, currentRelease])

  const handleTimeUpdate = () => {
    const audio = audioRef.current
    if (!audio) return
    const cur = audio.currentTime
    const dur = audio.duration
    const trackId = usePlayerStore.getState().currentTrack?.id

    usePlayerStore.setState({ currentTime: cur })
    updateMediaSessionPosition(dur, cur)
    if (trackId) playbackOnTimeUpdate(audio, trackId, cur, dur)

    if (dur && cur / dur > 0.75) {
      precacheNextTracks()
    }
  }

  const handleDurationChange = () => {
    if (!audioRef.current) return
    usePlayerStore.setState({ duration: audioRef.current.duration })
  }

  const handlePlaying = () => {
    updateMediaSessionPosition(
      audioRef.current?.duration || 0,
      audioRef.current?.currentTime || 0
    )
  }

  const handleError = () => {
    // Broken stream (e.g. Telegram 503 / ConnectionReset) fires 'error',
    // not 'ended' — without this the player stalls dead on one track and
    // the playlist never advances. Skip forward like onEnded does.
    const state = usePlayerStore.getState()
    if (!state.currentTrack) return
    console.warn('Audio stream error, skipping to next track')
    boundTrackIdRef.current = null
    state.nextTrack()
  }

  /**
   * Playlist auto-advance: must start the next file in this same event
   * callback — a later React effect is too late on Android 14 lockscreen.
   */
  const handleEnded = () => {
    const audio = audioRef.current
    const state = usePlayerStore.getState()
    const trackId = state.currentTrack?.id

    if (audio && trackId) {
      playbackOnEnded(
        trackId,
        audio.currentTime,
        Number.isFinite(audio.duration) ? audio.duration : null
      )
    }

    // Force re-bind restore hook for the next track id
    boundTrackIdRef.current = null
    state.nextTrack()
  }

  return (
    <div style={{ minHeight: '100dvh', backgroundColor: 'var(--bg)', color: 'var(--ink)' }}>
      {/* Hidden Native Audio Element — single node for the whole session */}
      <audio
        ref={audioRef}
        preload="auto"
        playsInline
        onTimeUpdate={handleTimeUpdate}
        onDurationChange={handleDurationChange}
        onEnded={handleEnded}
        onError={handleError}
        onPlaying={handlePlaying}
      />

      {/* Main Music Library View */}
      <MusicLibrary onOpenCoverPicker={setCoverTarget} />

      {/* Persistent Audio Player */}
      <MusicLibraryPlayer />

      {/* Authentication Modal */}
      <AuthModal />

      {/* Cover Art Picker Modal */}
      {coverTarget && (
        <CoverPicker
          target={coverTarget}
          onClose={() => setCoverTarget(null)}
          onUpdated={() => window.location.reload()}
        />
      )}
    </div>
  )
}
