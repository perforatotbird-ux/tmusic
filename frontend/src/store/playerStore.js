import { create } from 'zustand'
import { streamApi } from '../api/stream'
import { setMediaPlaybackState } from '../services/mediaSession'
import { IS_NATIVE } from '../services/nativeBridge'

const precachedTracks = new Set()
// In-flight prefetch aborters, keyed by track id: a new playQueue aborts
// previous prefetches so they stop consuming bandwidth/connections.
const prefetchAborts = new Map()

/** Retry timers for Android autoplay blocks after background/lockscreen. */
const resumeTimers = new Set()

function rememberPrecached(id) {
  precachedTracks.add(id)
  // Bounded: ids are tiny, but the set must not grow for the whole session.
  if (precachedTracks.size > 500) {
    precachedTracks.delete(precachedTracks.values().next().value)
  }
}

const storedVolume = parseFloat(localStorage.getItem('tmusic_volume') || '0.9')

export const usePlayerStore = create((set, get) => {
  /** Point <audio> at track and start playback in the SAME call stack (Android-safe). */
  function startAudio(fileId, { forceLoad = false } = {}) {
    const audio = get().audioElement
    if (!audio || fileId == null) return

    const url = streamApi.getStreamUrl(fileId)
    const key = String(fileId)

    // Re-bind src when track id changed (or forced) — keep the same <audio> node
    // so Android does not drop the Media Session between playlist items.
    if (forceLoad || audio.getAttribute('data-track-id') !== key) {
      audio.src = url
      audio.setAttribute('data-track-id', key)
      try {
        audio.load()
      } catch {
        /* ignore */
      }
    }

    setMediaPlaybackState('playing')

    const tryPlay = () => {
      if (get().currentTrack?.id !== fileId) return
      if (!get().isPlaying) return
      const p = audio.play()
      if (p && typeof p.catch === 'function') {
        p.catch(() => {
          if (get().currentTrack?.id !== fileId || !get().isPlaying) return
          const t = setTimeout(() => {
            resumeTimers.delete(t)
            if (get().currentTrack?.id === fileId && get().isPlaying && audio.paused) {
              const p2 = audio.play()
              if (p2 && p2.catch) p2.catch(() => armGlobalResume())
            }
          }, 250)
          resumeTimers.add(t)
          armGlobalResume()
        })
      }
    }

    if (audio.readyState >= 2) {
      tryPlay()
    } else {
      const onCanPlay = () => {
        audio.removeEventListener('canplay', onCanPlay)
        audio.removeEventListener('loadeddata', onCanPlay)
        tryPlay()
      }
      audio.addEventListener('canplay', onCanPlay)
      audio.addEventListener('loadeddata', onCanPlay)
      tryPlay()
    }
  }

  /** Resume on next user gesture / tab visible if OS blocked autoplay. */
  function armGlobalResume() {
    if (IS_NATIVE) return
    const handler = () => {
      const { isPlaying, currentTrack, audioElement } = get()
      if (!isPlaying || !currentTrack || !audioElement) {
        cleanup()
        return
      }
      if (audioElement.paused) {
        const p = audioElement.play()
        if (p && p.catch) p.catch(() => {})
        setMediaPlaybackState('playing')
      }
      if (!audioElement.paused) cleanup()
    }
    const onVis = () => {
      if (document.visibilityState === 'visible') handler()
    }
    function cleanup() {
      document.removeEventListener('click', handler)
      document.removeEventListener('touchstart', handler)
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', handler)
    }
    document.addEventListener('click', handler)
    document.addEventListener('touchstart', handler)
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', handler)
  }

  /** Resume watchdog: OS pause vs store isPlaying=true (lockscreen case). */
  function ensureWatchdog() {
    if (IS_NATIVE || get()._watchdogId) return
    const id = setInterval(() => {
      const { isPlaying, currentTrack, audioElement } = get()
      if (!isPlaying || !currentTrack || !audioElement) {
        clearInterval(id)
        set({ _watchdogId: null })
        return
      }
      if (audioElement.paused && audioElement.readyState >= 1) {
        const p = audioElement.play()
        if (p && p.catch) p.catch(() => armGlobalResume())
      }
    }, 1500)
    set({ _watchdogId: id })
  }

  return {
    currentTrack: null,
    currentRelease: null, // { title, artistName, coverUrl }
    queue: [],
    queueIndex: -1,
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    volume: Number.isFinite(storedVolume) ? storedVolume : 0.9,
    isMuted: false,
    shuffle: false,
    repeat: 'off', // 'off' | 'all' | 'one'
    isPlayerExpanded: false,
    audioElement: null,
    _watchdogId: null,

    setAudioElement: (el) => set({ audioElement: el }),
    setPlayerExpanded: (expanded) => set({ isPlayerExpanded: expanded }),

    playTrack: (track, releaseInfo = null) => {
      if (!track) return
      const { queue } = get()
      let idx = queue.findIndex((t) => t.id === track.id)
      if (idx === -1) {
        set({
          queue: [track],
          queueIndex: 0,
          currentTrack: track,
          currentRelease: releaseInfo,
          isPlaying: true,
          currentTime: 0,
        })
      } else {
        set({
          queueIndex: idx,
          currentTrack: track,
          currentRelease: releaseInfo || get().currentRelease,
          isPlaying: true,
          currentTime: 0,
        })
      }
      startAudio(track.id)
      ensureWatchdog()
    },

    playQueue: (tracks, startIndex = 0, releaseInfo = null) => {
      if (!tracks || tracks.length === 0) return
      // New queue: previous prefetches belong to an obsolete queue — abort them.
      for (const ctrl of prefetchAborts.values()) {
        try { ctrl.abort() } catch { /* ignore */ }
      }
      prefetchAborts.clear()
      const current = tracks[startIndex] || tracks[0]
      set({
        queue: tracks,
        queueIndex: startIndex,
        currentTrack: current,
        currentRelease: releaseInfo,
        isPlaying: true,
        currentTime: 0,
      })
      startAudio(current.id, { forceLoad: true })
      ensureWatchdog()
      get().precacheNextTracks()
    },

    togglePlay: () => {
      const { isPlaying, audioElement, currentTrack } = get()
      if (!audioElement || !currentTrack) return
      if (isPlaying && !audioElement.paused) {
        audioElement.pause()
        set({ isPlaying: false })
        setMediaPlaybackState('paused')
      } else {
        set({ isPlaying: true })
        // Resume existing buffer or re-bind src
        if (!audioElement.getAttribute('data-track-id')) {
          startAudio(currentTrack.id)
        } else {
          setMediaPlaybackState('playing')
          const p = audioElement.play()
          if (p && p.catch) p.catch(() => armGlobalResume())
        }
        ensureWatchdog()
      }
    },

    nextTrack: () => {
      const { queue, queueIndex, repeat, shuffle, audioElement } = get()
      if (queue.length === 0) return

      if (repeat === 'one') {
        if (audioElement) {
          audioElement.currentTime = 0
          set({ isPlaying: true, currentTime: 0 })
          setMediaPlaybackState('playing')
          const p = audioElement.play()
          if (p && p.catch) p.catch(() => armGlobalResume())
        }
        return
      }

      let nextIdx
      if (shuffle && queue.length > 1) {
        nextIdx = Math.floor(Math.random() * queue.length)
        if (nextIdx === queueIndex) nextIdx = (nextIdx + 1) % queue.length
      } else {
        nextIdx = queueIndex + 1
        if (nextIdx >= queue.length) {
          if (repeat === 'all') {
            nextIdx = 0
          } else {
            set({ isPlaying: false })
            setMediaPlaybackState('none')
            return
          }
        }
      }

      const next = queue[nextIdx]
      if (!next) {
        set({ isPlaying: false })
        return
      }

      // Update state + start audio SYNCHRONOUSLY (critical for Android lockscreen).
      set({
        queueIndex: nextIdx,
        currentTrack: next,
        isPlaying: true,
        currentTime: 0,
        duration: 0,
      })
      startAudio(next.id, { forceLoad: true })
      ensureWatchdog()
      get().precacheNextTracks()
    },

    prevTrack: () => {
      const { queue, queueIndex, currentTime, audioElement } = get()
      if (queue.length === 0) return

      if (currentTime > 3 && audioElement) {
        audioElement.currentTime = 0
        set({ currentTime: 0 })
        return
      }

      const prevIdx = queueIndex - 1 < 0 ? queue.length - 1 : queueIndex - 1
      const prev = queue[prevIdx]
      if (!prev) return
      set({ queueIndex: prevIdx, currentTrack: prev, isPlaying: true, currentTime: 0 })
      startAudio(prev.id, { forceLoad: true })
      ensureWatchdog()
    },

    seek: (seconds) => {
      const { audioElement } = get()
      if (audioElement) {
        audioElement.currentTime = seconds
        set({ currentTime: seconds })
      }
    },

    setVolume: (val) => {
      const clamped = Math.max(0, Math.min(1, val))
      localStorage.setItem('tmusic_volume', clamped.toString())
      const { audioElement } = get()
      if (audioElement) audioElement.volume = clamped
      set({ volume: clamped, isMuted: clamped === 0 })
    },

    toggleMute: () => {
      const { isMuted, volume, audioElement } = get()
      if (isMuted) {
        const restored = volume || 0.8
        if (audioElement) audioElement.volume = restored
        set({ isMuted: false, volume: restored })
      } else {
        if (audioElement) audioElement.volume = 0
        set({ isMuted: true, volume: 0 })
      }
    },

    toggleShuffle: () => set((s) => ({ shuffle: !s.shuffle })),

    cycleRepeat: () => {
      const { repeat } = get()
      const modes = ['off', 'all', 'one']
      const next = modes[(modes.indexOf(repeat) + 1) % modes.length]
      set({ repeat: next })
    },

    /** Manual start from UI after unlock — used by player button. */
    forceResume: () => {
      const { currentTrack, audioElement } = get()
      if (!currentTrack || !audioElement) return
      set({ isPlaying: true })
      if (audioElement.getAttribute('data-track-id') !== String(currentTrack.id) || !audioElement.src) {
        startAudio(currentTrack.id, { forceLoad: true })
      } else {
        setMediaPlaybackState('playing')
        const p = audioElement.play()
        if (p && p.catch) p.catch(() => {})
      }
      ensureWatchdog()
    },

    precacheNextTracks: () => {
      const { queue, queueIndex } = get()
      if (!queue || queue.length === 0) return

      // Pre-cache next 2 tracks in queue
      const targets = [queue[queueIndex + 1], queue[queueIndex + 2]].filter(Boolean)
      for (const track of targets) {
        if (precachedTracks.has(track.id) || prefetchAborts.has(track.id)) continue
        rememberPrecached(track.id)

        const ctrl = new AbortController()
        prefetchAborts.set(track.id, ctrl)
        const url = streamApi.getStreamUrl(track.id)
        // Fetch initial 2MB chunk with Range header. The body is consumed (into
        // the HTTP cache) so the connection is released instead of dangling.
        fetch(url, {
          headers: { Range: 'bytes=0-2097152' },
          signal: ctrl.signal,
        })
          .then((resp) => resp.arrayBuffer().catch(() => {}))
          .catch(() => {
            // Silent background pre-cache error (incl. abort on queue change)
            precachedTracks.delete(track.id)
          })
          .finally(() => {
            if (prefetchAborts.get(track.id) === ctrl) {
              prefetchAborts.delete(track.id)
            }
          })
      }
    },
  }
})
