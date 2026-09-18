import React, { useEffect, useRef, useState } from 'react'
import { useAuthStore } from './store/authStore'
import { usePlayerStore } from './store/playerStore'
import { setupMediaSession, updateMediaSessionMetadata, updateMediaSessionPosition } from './services/mediaSession'
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
    checkAuth()
    setupMediaSession(usePlayerStore)

    const handleUnauthorized = () => setAuthModalOpen(true)
    window.addEventListener('tmusic:unauthorized', handleUnauthorized)

    // Android lockscreen / tab throttle: resume if we believe playback continues.
    const resumeIfNeeded = () => {
      if (document.visibilityState && document.visibilityState !== 'visible') return
      const { isPlaying, currentTrack: track, audioElement } = usePlayerStore.getState()
      if (isPlaying && track && audioElement && audioElement.paused) {
        const p = audioElement.play()
        if (p && p.catch) p.catch(() => {})
      }
    }
    const onVis = () => resumeIfNeeded()
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', resumeIfNeeded)

    return () => {
      window.removeEventListener('tmusic:unauthorized', handleUnauthorized)
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', resumeIfNeeded)
    }
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
