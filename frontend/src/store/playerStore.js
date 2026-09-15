import { create } from 'zustand'
import { streamApi } from '../api/stream'

const precachedTracks = new Set()

const storedVolume = parseFloat(localStorage.getItem('tmusic_volume') || '0.9')

export const usePlayerStore = create((set, get) => ({
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

  setAudioElement: (el) => set({ audioElement: el }),
  setPlayerExpanded: (expanded) => set({ isPlayerExpanded: expanded }),

  playTrack: (track, releaseInfo = null) => {
    const { queue } = get()
    let idx = queue.findIndex((t) => t.id === track.id)
    if (idx === -1) {
      set({ queue: [track], queueIndex: 0, currentTrack: track, currentRelease: releaseInfo, isPlaying: true })
    } else {
      set({ queueIndex: idx, currentTrack: track, currentRelease: releaseInfo || get().currentRelease, isPlaying: true })
    }
  },

  playQueue: (tracks, startIndex = 0, releaseInfo = null) => {
    if (!tracks || tracks.length === 0) return
    const current = tracks[startIndex] || tracks[0]
    set({
      queue: tracks,
      queueIndex: startIndex,
      currentTrack: current,
      currentRelease: releaseInfo,
      isPlaying: true,
      currentTime: 0,
    })
  },

  togglePlay: () => {
    const { isPlaying, audioElement } = get()
    if (!audioElement) return
    if (isPlaying) {
      audioElement.pause()
      set({ isPlaying: false })
    } else {
      audioElement.play().catch(() => {})
      set({ isPlaying: true })
    }
  },

  nextTrack: () => {
    const { queue, queueIndex, repeat, shuffle } = get()
    if (queue.length === 0) return

    if (repeat === 'one') {
      const { audioElement } = get()
      if (audioElement) {
        audioElement.currentTime = 0
        audioElement.play().catch(() => {})
      }
      return
    }

    let nextIdx
    if (shuffle) {
      nextIdx = Math.floor(Math.random() * queue.length)
    } else {
      nextIdx = queueIndex + 1
      if (nextIdx >= queue.length) {
        if (repeat === 'all') {
          nextIdx = 0
        } else {
          set({ isPlaying: false })
          return
        }
      }
    }

    const next = queue[nextIdx]
    set({ queueIndex: nextIdx, currentTrack: next, isPlaying: true, currentTime: 0 })
    get().precacheNextTracks()
  },

  prevTrack: () => {
    const { queue, queueIndex, currentTime, audioElement } = get()
    if (queue.length === 0) return

    if (currentTime > 3 && audioElement) {
      audioElement.currentTime = 0
      return
    }

    const prevIdx = queueIndex - 1 < 0 ? queue.length - 1 : queueIndex - 1
    const prev = queue[prevIdx]
    set({ queueIndex: prevIdx, currentTrack: prev, isPlaying: true, currentTime: 0 })
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
      set({ isMuted: true })
    }
  },

  toggleShuffle: () => set((s) => ({ shuffle: !s.shuffle })),

  cycleRepeat: () => {
    const { repeat } = get()
    const modes = ['off', 'all', 'one']
    const next = modes[(modes.indexOf(repeat) + 1) % modes.length]
    set({ repeat: next })
  },

  precacheNextTracks: () => {
    const { queue, queueIndex } = get()
    if (!queue || queue.length === 0) return

    // Pre-cache next 2 tracks in queue
    const targets = [queue[queueIndex + 1], queue[queueIndex + 2]].filter(Boolean)
    for (const track of targets) {
      if (precachedTracks.has(track.id)) continue
      precachedTracks.add(track.id)

      const url = streamApi.getStreamUrl(track.id)
      // Fetch initial 2MB chunk with Range header
      fetch(url, {
        headers: { Range: 'bytes=0-2097152' },
      }).catch((e) => {
        // Silent background pre-cache error
        precachedTracks.delete(track.id)
      })
    }
  },
}))
