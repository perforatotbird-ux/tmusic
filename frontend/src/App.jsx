import React, { useEffect, useRef, useState } from 'react'
import { useAuthStore } from './store/authStore'
import { usePlayerStore } from './store/playerStore'
import { streamApi } from './api/stream'
import { setupMediaSession, updateMediaSessionMetadata, updateMediaSessionPosition } from './services/mediaSession'
import { AuthModal } from './components/AuthModal'
import { MusicLibrary } from './components/MusicLibrary'
import { MusicLibraryPlayer } from './components/MusicLibraryPlayer'
import { CoverPicker } from './components/CoverPicker'

export function App() {
  const audioRef = useRef(null)
  const [coverTarget, setCoverTarget] = useState(null)

  const { checkAuth, setAuthModalOpen } = useAuthStore()
  const {
    currentTrack,
    currentRelease,
    isPlaying,
    setAudioElement,
    nextTrack,
    precacheNextTracks,
  } = usePlayerStore()

  useEffect(() => {
    checkAuth()
    setupMediaSession(usePlayerStore)

    const handleUnauthorized = () => setAuthModalOpen(true)
    window.addEventListener('tmusic:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('tmusic:unauthorized', handleUnauthorized)
  }, [])

  useEffect(() => {
    if (audioRef.current) {
      setAudioElement(audioRef.current)
    }
  }, [audioRef.current])

  // Track source update
  useEffect(() => {
    if (!audioRef.current || !currentTrack) return

    const streamUrl = streamApi.getStreamUrl(currentTrack.id)
    audioRef.current.src = streamUrl
    audioRef.current.load()
    if (isPlaying) {
      audioRef.current.play().catch(() => {})
    }

    updateMediaSessionMetadata(currentTrack, currentRelease)
  }, [currentTrack?.id])

  // Play/Pause sync
  useEffect(() => {
    if (!audioRef.current) return
    if (isPlaying && audioRef.current.paused) {
      audioRef.current.play().catch(() => {})
    } else if (!isPlaying && !audioRef.current.paused) {
      audioRef.current.pause()
    }
  }, [isPlaying])

  const handleTimeUpdate = () => {
    if (!audioRef.current) return
    const cur = audioRef.current.currentTime
    const dur = audioRef.current.duration

    usePlayerStore.setState({ currentTime: cur })
    updateMediaSessionPosition(dur, cur)

    // Trigger background pre-cache when reached 75% of track
    if (dur && cur / dur > 0.75) {
      precacheNextTracks()
    }
  }

  const handleDurationChange = () => {
    if (!audioRef.current) return
    usePlayerStore.setState({ duration: audioRef.current.duration })
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg)', color: 'var(--ink)' }}>
      {/* Hidden Native Audio Element */}
      <audio
        ref={audioRef}
        onTimeUpdate={handleTimeUpdate}
        onDurationChange={handleDurationChange}
        onEnded={nextTrack}
        preload="auto"
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
