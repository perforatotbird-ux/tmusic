import React, { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useAuthStore } from './store/authStore'
import { usePlayerStore } from './store/playerStore'
import { useFavoritesStore } from './store/favoritesStore'
import { setupMediaSession, updateMediaSessionMetadata, updateMediaSessionPosition, setMediaPlaybackState } from './services/mediaSession'
import { IS_NATIVE } from './services/nativeBridge'
import {
  onTrackChanged,
  tryRestorePosition,
  onTimeUpdate as playbackOnTimeUpdate,
  onPause as playbackOnPause,
  onEnded as playbackOnEnded,
} from './services/playbackProgress'
import { AuthModal } from './components/AuthModal'
import { PasswordChangeModal } from './components/PasswordChangeModal'
import { MusicLibrary } from './components/MusicLibrary'
import { MusicLibraryPlayer } from './components/MusicLibraryPlayer'
import { CoverPicker } from './components/CoverPicker'
const StorageSettingsModal = lazy(() =>
  import('./components/StorageSettingsModal').then((m) => ({ default: m.StorageSettingsModal }))
)
import { ScanProgressHost } from './components/ScanProgressHost'
import { ToastHost } from './components/ui/ToastHost'
import { useOfflineStore } from './store/offlineStore'
import { createLogger } from './utils/logger'
import { WifiOff, HardDrive } from 'lucide-react'

const log = createLogger('app')

export function App() {
  const audioRef = useRef(null)
  const favoriteError = useFavoritesStore((state) => state.error)
  const [coverTarget, setCoverTarget] = useState(null)
  const [passwordChangeOpen, setPasswordChangeOpen] = useState(false)
  // Track id we've already bound restore/metadata for (store owns src/play).
  const boundTrackIdRef = useRef(null)
  // M-04: the fixed offline banner overlaps the sticky topbar unless the
  // topbar is pushed down by its real height (it wraps to two lines on
  // narrow phones, so a hardcoded value would lie).
  const bannerRef = useRef(null)

  const { storageModalOpen, setStorageModalOpen, isOffline, cachedTrackIds } = useOfflineStore()

  // M-04: keep --offline-banner-h in sync with the real banner height so the
  // sticky topbar (MusicLibrary) and the scan-progress host sit below it.
  useEffect(() => {
    const root = document.documentElement
    if (!isOffline) {
      root.style.setProperty('--offline-banner-h', '0px')
      return undefined
    }
    const update = () => {
      const h = bannerRef.current?.offsetHeight || 0
      root.style.setProperty('--offline-banner-h', `${h}px`)
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [isOffline, cachedTrackIds.size])

  useEffect(() => {
    const offlineMgr = useOfflineStore.getState()
    offlineMgr.init()
    return () => offlineMgr.dispose()
  }, [])

  const { user, isAuthenticated, checkAuth, setAuthModalOpen } = useAuthStore()
  const isPreparing = usePlayerStore((state) => state.isPreparing)

  // Kick audio playback as soon as preparation finishes and real MP3 is bound
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const state = usePlayerStore.getState()
    // `data-track-id` is only present once a real track source is bound; while
    // preparing, the element holds the silent pre-roll (blob: URL) and must not
    // be started.
    if (state.isPlaying && !isPreparing && audio.paused && audio.src && audio.getAttribute('data-track-id')) {
      const p = audio.play()
      if (p && p.catch) {
        p.catch((err) => log.warn('playback did not resume after preparation', { error: err?.name || String(err) }))
      }
    }
  }, [isPreparing])

  useEffect(() => {
    if (isAuthenticated && !user?.password_change_required) {
      usePlayerStore.getState().startTranscodeEvents()
    }
    return () => usePlayerStore.getState().stopTranscodeEvents()
  }, [isAuthenticated, user?.password_change_required])

  useEffect(() => {
    const openPasswordChange = () => setPasswordChangeOpen(true)
    window.addEventListener('tmusic:change-password', openPasswordChange)
    return () => window.removeEventListener('tmusic:change-password', openPasswordChange)
  }, [])
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
      .catch((e) => log.debug('system config fetch failed', { error: e?.message }))
  }, [])

  useEffect(() => {
    log.info('App mounted, checking session')
    checkAuth()
    const disposeMediaSession = setupMediaSession(usePlayerStore)

    const handleUnauthorized = () => {
      log.warn('received tmusic:unauthorized → opening auth modal')
      setAuthModalOpen(true)
      usePlayerStore.getState().stopTranscodeEvents()
    }
    window.addEventListener('tmusic:unauthorized', handleUnauthorized)

    // UI-09: surface rate-limit errors as a recoverable notice instead of a silent
    // freeze (the request already fails fast in api/client.js).
    const handleRateLimited = (e) => {
      const retryAfter = e?.detail?.retryAfter ?? 60
      usePlayerStore.getState().pushNotice({
        kind: 'warn',
        text: `Слишком много запросов. Повторите через ${retryAfter} секунд.`,
      })
    }
    window.addEventListener('tmusic:rate-limited', handleRateLimited)

    // Android lockscreen / tab throttle: resume if we believe playback continues.
    // In native WebView with ForegroundService, skip this to avoid race conditions.
    let onVis = null
    let resumeIfNeeded = null

    if (!IS_NATIVE) {
      resumeIfNeeded = () => {
        if (document.visibilityState && document.visibilityState !== 'visible') return
        const { isPlaying, currentTrack: track, audioElement } = usePlayerStore.getState()
        if (isPlaying && !usePlayerStore.getState().isPreparing && track && audioElement && audioElement.paused) {
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
      disposeMediaSession()
      window.removeEventListener('tmusic:unauthorized', handleUnauthorized)
      window.removeEventListener('tmusic:rate-limited', handleRateLimited)
      if (onVis) document.removeEventListener('visibilitychange', onVis)
      if (resumeIfNeeded) window.removeEventListener('focus', resumeIfNeeded)
    }
  }, [])

  // Global player hotkeys: Space, ←/→ seek, Shift+←/→ track, M mute, L like.
  useEffect(() => {
    // UI-03: this previously checked only text fields, so the global Space
    // handler called preventDefault() while a <button> had focus and cancelled
    // its native activation — no button in the app could be pressed with Space.
    const isTypingTarget = (el) => {
      if (!el) return false
      const tag = el.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
      if (el.isContentEditable) return true
      // Buttons, links and anything acting as one own Space/Enter themselves.
      if (tag === 'BUTTON' || tag === 'A') return true
      if (el.getAttribute?.('role') === 'button') return true
      return Boolean(el.closest?.('[role="button"],[role="tab"],[role="menuitem"]'))
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
        if (s.isPlaying && !s.isPreparing && audio?.paused) s.forceResume()
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
      // ArrowUp/Down own volume only when focus is on the page background;
      // otherwise they must scroll / move through lists.
      const volumeKeysAllowed = e.target === document.body || e.target === document.documentElement
      if (key === 'ArrowUp') {
        if (!volumeKeysAllowed) return
        e.preventDefault()
        s.setVolume(Math.min(1, (s.isMuted ? 0 : s.volume) + 0.05))
        return
      }
      if (key === 'ArrowDown') {
        if (!volumeKeysAllowed) return
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
    const audio = audioRef.current
    if (audio) setAudioElement(audio)
    return () => {
      if (usePlayerStore.getState().audioElement === audio) {
        usePlayerStore.getState().dispose()
        boundTrackIdRef.current = null
      }
    }
  }, [setAudioElement])

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
    if (state.isPlaying && !state.isPreparing && audio.paused) {
      const p = audio.play()
      if (p && p.catch) p.catch(() => {})
    }

    return () => audio.removeEventListener('loadedmetadata', restoreHandler)
  }, [currentTrack?.id, currentRelease])

  const handleTimeUpdate = () => {
    if (usePlayerStore.getState().isPreparing) return
    const audio = audioRef.current
    if (!audio) return
    const cur = audio.currentTime
    const dur = audio.duration
    const trackId = usePlayerStore.getState().currentTrack?.id

    usePlayerStore.setState({
      currentTime: cur,
      duration: Number.isFinite(dur) && dur > 0 ? dur : usePlayerStore.getState().duration,
    })
    // Keep the localStorage session snapshot fresh (internally throttled ~4s).
    usePlayerStore.getState().persistSession()
    updateBuffered()
    updateMediaSessionPosition(dur, cur)
    if (trackId) {
      playbackOnTimeUpdate(audio, trackId, cur, dur)
      if (Number.isFinite(dur) && dur > 0) usePlayerStore.getState().rememberDuration(trackId, dur)
    }

    if (dur && cur / dur > 0.75) {
      precacheNextTracks()
    }
  }

  /** Highest end of the buffered ranges that covers (or follows) the playhead. */
  const updateBuffered = () => {
    const audio = audioRef.current
    if (!audio) return
    let upTo = 0
    const { buffered, currentTime } = audio
    for (let i = 0; i < buffered.length; i++) {
      if (buffered.start(i) <= currentTime + 0.5) {
        upTo = Math.max(upTo, buffered.end(i))
      }
    }
    if (Math.abs(upTo - usePlayerStore.getState().bufferedUpTo) > 0.5) {
      usePlayerStore.getState().setBufferedUpTo(upTo)
    }
  }

  const handleDurationChange = () => {
    if (!audioRef.current) return
    const dur = audioRef.current.duration
    if (Number.isFinite(dur) && dur > 0) {
      usePlayerStore.setState({ duration: dur })
      const trackId = usePlayerStore.getState().currentTrack?.id
      if (trackId) usePlayerStore.getState().rememberDuration(trackId, dur)
    }
  }

  const handleWaiting = () => {
    if (usePlayerStore.getState().isPlaying && !usePlayerStore.getState().isPreparing) {
      usePlayerStore.getState().setBuffering(true)
    }
  }

  const handlePlaying = () => {
    if (usePlayerStore.getState().isPreparing) return
    usePlayerStore.getState().setBuffering(false)
    updateMediaSessionPosition(
      audioRef.current?.duration || 0,
      audioRef.current?.currentTime || 0
    )
    // Playback of the current track actually started → NOW enqueue exactly
    // the next 2 queue tracks (shuffle-aware) for background transcoding.
    // The current track itself is already being transcoded via its own
    // playback GET. Nothing else is ever enqueued: no queue-wide bulk work.
    usePlayerStore.getState().prefetchNextTranscodes()
  }

  const handleError = () => {
    // Preparation and browser decode errors are not playlist completion.
    usePlayerStore.getState().recoverAudioError()
  }

  const handlePause = () => {
    const audio = audioRef.current
    const trackId = usePlayerStore.getState().currentTrack?.id
    if (audio && trackId) {
      playbackOnPause(
        trackId,
        audio.currentTime,
        Number.isFinite(audio.duration) ? audio.duration : null
      )
    }
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
      {/* Offline Status Top Banner */}
      {isOffline && (
        <div
          ref={bannerRef}
          role="status"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            zIndex: 'var(--z-offline)',
            background: 'linear-gradient(90deg, #ea580c, #c2410c)',
            color: '#fff',
            /* UI-13: honour the notch/status bar and reserve space below via
               --offline-banner-h so the banner stops covering the topbar. */
            padding: 'max(8px, var(--safe-top)) max(16px, var(--safe-right)) 8px max(16px, var(--safe-left))',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 8,
            flexWrap: 'wrap',
            fontSize: '12.5px',
            fontWeight: '700',
            boxShadow: '0 2px 10px rgba(0,0,0,0.5)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <WifiOff size={16} />
            <span>Офлайн режим • Доступно {cachedTrackIds.size} закешированных треков</span>
          </div>
          <button
            type="button"
            onClick={() => setStorageModalOpen(true)}
            title="Кэш и память"
            aria-label="Открыть настройки кэша и памяти"
            style={{
              background: 'rgba(255,255,255,0.2)',
              border: 'none',
              borderRadius: '6px',
              // UI-19: was ~21px tall — far below the 44px touch minimum.
              minHeight: 44,
              padding: '4px 12px',
              color: '#fff',
              fontSize: '11.5px',
              fontWeight: '800',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <HardDrive size={13} /> Кэш и память
          </button>
        </div>
      )}

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
        onPause={handlePause}
      />

      {/* Main Music Library View */}
      <MusicLibrary onOpenCoverPicker={setCoverTarget} />

      {/* Persistent Audio Player */}
      <MusicLibraryPlayer />

      {/* UI-12: the favourites error used a hardcoded bottom:100 and zIndex 9998 —
          it collided with the player bar / toasts on mobile. Route everything
          through the shared ToastHost, which owns the stacking layer. */}
      {favoriteError && (
        <ToastHost.FavoriteError
          message={favoriteError}
          onRetry={() => useFavoritesStore.getState().load({ force: true })}
          onClose={() => useFavoritesStore.getState().clearError()}
        />
      )}
      {/* Authentication Modal */}
      <AuthModal />
      {user && (passwordChangeOpen || user.password_change_required) && (
        <PasswordChangeModal
          required={Boolean(user.password_change_required)}
          onClose={() => setPasswordChangeOpen(false)}
        />
      )}

      {/* Cover Art Picker Modal */}
      {coverTarget && (
        <CoverPicker
          // Remount per target: a stale `url`/`preview` from the previously
          // edited row must never be submitted for a different artist/album.
          key={`${coverTarget.type}:${coverTarget.id}`}
          target={coverTarget}
          onClose={() => setCoverTarget(null)}
          // UI-08: no hard reload — it killed playback, the queue and the Media
          // Session. The API layer emits `tmusic:catalog-changed` with the
          // touched row; MusicLibrary / AdminCatalog listen and refresh in
          // place.
          onUpdated={() => {}}
        />
      )}

      {/* Telegram-style Storage & Cache Settings Modal */}
      {storageModalOpen && (
        <Suspense fallback={null}>
          <StorageSettingsModal onClose={() => setStorageModalOpen(false)} />
        </Suspense>
      )}

      {/* Catalog scans run as background jobs: their progress lives here so it
          survives navigating away from the admin panel mid-scan. */}
      <ScanProgressHost />
    </div>
  )
}
