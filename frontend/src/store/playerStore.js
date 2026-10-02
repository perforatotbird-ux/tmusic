import { create } from 'zustand'
import { streamApi } from '../api/stream'
import { setMediaPlaybackState } from '../services/mediaSession'
import { IS_NATIVE } from '../services/nativeBridge'
import { createLogger } from '../utils/logger'
import { useOfflineStore } from './offlineStore'
import { getCachedTrack } from '../services/offlineStorageService'

const log = createLogger('player')

const precachedTracks = new Set()
const prefetchAborts = new Map()

/** Retry timers for Android autoplay blocks after background/lockscreen. */
const resumeTimers = new Set()

let activeBlobUrl = null
function cleanupBlobUrl() {
  if (activeBlobUrl) {
    try {
      URL.revokeObjectURL(activeBlobUrl)
    } catch {
      /* ignore */
    }
    activeBlobUrl = null
  }
}

// ─── Background Transcoding SSE Listener ─────────────────────────────────────────────────
let transcodeEventSource = null
const pendingTranscodeNotifications = new Set()
const transcodeNotificationTimers = new Set()

function initTranscodeSSE() {
  if (IS_NATIVE || transcodeEventSource) return
  transcodeEventSource = streamApi.createTranscodeEventSource()

  transcodeEventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.event === 'ping') return

      const state = usePlayerStore.getState()
      const track = state.queue.find((t) => t.id === data.file_id)

      if (data.event === 'transcode_complete' && track) {
        const msg = `Готово: "${track.title || track.name}" теперь в MP3 320kbps`
        usePlayerStore.getState().pushNotice({ kind: 'info', text: msg })
        pendingTranscodeNotifications.add(data.file_id)
        const timer = setTimeout(() => {
          transcodeNotificationTimers.delete(timer)
          pendingTranscodeNotifications.delete(data.file_id)
        }, 10000)
        transcodeNotificationTimers.add(timer)
      } else if (data.event === 'transcode_started' && track) {
        const msg = `Транскодинг: "${track.title || track.name}"`
        usePlayerStore.getState().pushNotice({ kind: 'info', text: msg })
      } else if (data.event === 'transcode_failed' && track) {
        const msg = `Ошибка транскодинга: "${track.title || track.name}"`
        usePlayerStore.getState().pushNotice({ kind: 'error', text: msg })
      } else if (data.event === 'transcode_prefetch' && track) {
        log.debug('Started prefetching transcode for track', { id: data.file_id })
      }
    } catch (e) {
      log.debug('SSE parse error', { error: e.message })
    }
  }

  transcodeEventSource.onerror = (err) => {
    // EventSource performs its own bounded browser-managed reconnect. Creating
    // another instance here would leave the old one alive and multiply SSE
    // connections and server-side queues after every transient failure.
    log.debug('Transcode SSE error; browser will reconnect automatically', err)
  }
}

function stopTranscodeSSE() {
  if (transcodeEventSource) {
    transcodeEventSource.onmessage = null
    transcodeEventSource.onerror = null
    transcodeEventSource.close()
    transcodeEventSource = null
  }
  for (const timer of transcodeNotificationTimers) clearTimeout(timer)
  transcodeNotificationTimers.clear()
  pendingTranscodeNotifications.clear()
}

function rememberPrecached(id) {
  precachedTracks.add(id)
  // Bounded: ids are tiny, but the set must not grow for the whole session.
  if (precachedTracks.size > 500) {
    precachedTracks.delete(precachedTracks.values().next().value)
  }
}

function getOfflineStoreState() {
  try {
    if (typeof useOfflineStore !== 'undefined' && typeof useOfflineStore.getState === 'function') {
      return useOfflineStore.getState()
    }
  } catch {}
  return null
}

function fetchCachedTrackSafe(fileId) {
  try {
    if (typeof getCachedTrack === 'function') {
      return getCachedTrack(fileId)
    }
  } catch {}
  return Promise.resolve(null)
}

const storedVolume = parseFloat(localStorage.getItem('tmusic_volume') || '0.9')
const initialVolume = Number.isFinite(storedVolume) ? Math.max(0, Math.min(1, storedVolume)) : 0.9
const SESSION_KEY = 'tmusic_session'
const DURATIONS_KEY = 'tmusic_durations'
const SESSION_QUEUE_CAP = 600
/** UI-22: max simultaneously visible toasts. */
const MAX_NOTICES = 3
// First two upcoming tracks: 2 MB each (server PRECACHE_CHUNK_BYTES). The
// third: a small chunk, cheap insurance against a Telegram stall.
const PRECACHE_FULL_BYTES = 2 * 1024 * 1024
const PRECACHE_TAIL_BYTES = 512 * 1024

function readStoredDurations() {
  try {
    const raw = localStorage.getItem(DURATIONS_KEY)
    if (!raw) return {}
    const obj = JSON.parse(raw)
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

let durationsCache = readStoredDurations()

function persistDurations() {
  try {
    // Bound the map: keep the newest 400 entries by insertion order.
    const keys = Object.keys(durationsCache)
    if (keys.length > 400) {
      for (const k of keys.slice(0, keys.length - 400)) delete durationsCache[k]
    }
    localStorage.setItem(DURATIONS_KEY, JSON.stringify(durationsCache))
  } catch {
    /* ignore */
  }
}

/** Fisher-Yates: shuffled playback order of queue indices, current track first. */
function buildShuffleOrder(len, currentIdx) {
  const rest = []
  for (let i = 0; i < len; i++) if (i !== currentIdx) rest.push(i)
  for (let i = rest.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[rest[i], rest[j]] = [rest[j], rest[i]]
  }
  return currentIdx >= 0 && currentIdx < len ? [currentIdx, ...rest] : rest
}

export const usePlayerStore = create((set, get) => {
  // Shuffle permutation state (indices into queue). Kept beside the store via
  // closures: order = permutation of 0..queue.length-1, orderPos = our slot.
  let shuffleOrder = []
  let orderPos = -1
  let lastAudibleVolume = initialVolume > 0 ? initialVolume : 0.9
  let resumeCleanup = null
  let readyCleanup = null
  let audioGeneration = 0

  function clearRecovery(stopWatchdog = true) {
    audioGeneration += 1
    if (resumeCleanup) resumeCleanup()
    if (readyCleanup) readyCleanup()
    for (const timer of resumeTimers) clearTimeout(timer)
    resumeTimers.clear()
    if (stopWatchdog) {
      const id = get()._watchdogId
      if (id != null) clearInterval(id)
      set({ _watchdogId: null })
    }
  }

  function syncShuffle(len, currentIdx, enabled) {
    if (!enabled || len === 0) {
      shuffleOrder = []
      orderPos = -1
      return
    }
    shuffleOrder = buildShuffleOrder(len, currentIdx)
    orderPos = currentIdx >= 0 && currentIdx < len ? 0 : -1
  }

  /** Point <audio> at track and start playback in the SAME call stack (Android-safe). */
  function startAudio(fileId, { forceLoad = false, autoplay = true } = {}) {
    const audio = get().audioElement
    if (!audio || fileId == null) return
    clearRecovery(!autoplay)
    const generation = audioGeneration

    const isOffline = typeof navigator !== 'undefined' && !navigator.onLine
    const offlineState = getOfflineStoreState()
    const isCached = offlineState ? offlineState.cachedTrackIds.has(Number(fileId)) : false

    if (isOffline && !isCached) {
      log.warn('Track is not cached for offline playback', { id: fileId })
      get().setNotice({
        kind: 'error',
        text: 'Трек не закеширован для воспроизведения офлайн',
      })
      usePlayerStore.setState({ isPlaying: false, isBuffering: false })
      return
    }

    const key = String(fileId)
    const needsLoad = forceLoad || audio.getAttribute('data-track-id') !== key

    function tryPlay() {
      if (generation !== audioGeneration || get().currentTrack?.id !== fileId) return
      if (!get().isPlaying) return
      const p = audio.play()
      if (p && typeof p.catch === 'function') {
        p.catch(() => {
          if (generation !== audioGeneration || get().currentTrack?.id !== fileId || !get().isPlaying) return
          const t = setTimeout(() => {
            resumeTimers.delete(t)
            if (generation === audioGeneration && get().currentTrack?.id === fileId && get().isPlaying && audio.paused) {
              const p2 = audio.play()
              if (p2 && p2.catch) p2.catch(() => {
                if (generation === audioGeneration) armGlobalResume()
              })
            }
          }, 250)
          resumeTimers.add(t)
          armGlobalResume()
        })
      }
    }

    function fallbackStream() {
      if (generation !== audioGeneration || get().currentTrack?.id !== fileId) return
      const url = streamApi.getStreamUrl(fileId)
      audio.src = url
      audio.setAttribute('data-track-id', key)
      audio.removeAttribute('data-is-cached')
      usePlayerStore.setState({ isPlayingFromCache: false })
      try {
        audio.load()
      } catch {
        /* ignore */
      }
      streamApi.triggerTranscode(fileId).catch((err) => {
        log.debug('triggerTranscode failed', { id: fileId, error: err.message })
      })

      // Auto-cache played track if configured in offline settings
      const offlineSettings = offlineState?.settings
      if (offlineSettings?.autoCacheOnPlay && offlineSettings?.cacheAudio) {
        const track = get().currentTrack
        if (track && track.id === fileId) {
          offlineState.cacheTrack(track).catch(() => {})
        }
      }

      if (autoplay && get().isPlaying) {
        triggerPlayback()
      }
    }

    function triggerPlayback() {
      if (audio.readyState >= 2) {
        tryPlay()
      } else {
        const onCanPlay = () => {
          if (readyCleanup) readyCleanup()
          tryPlay()
        }
        readyCleanup = () => {
          audio.removeEventListener('canplay', onCanPlay)
          audio.removeEventListener('loadeddata', onCanPlay)
          readyCleanup = null
        }
        audio.addEventListener('canplay', onCanPlay)
        audio.addEventListener('loadeddata', onCanPlay)
        tryPlay()
      }
    }

    if (needsLoad) {
      cleanupBlobUrl()

      if (isCached) {
        fetchCachedTrackSafe(fileId).then((cached) => {
          if (generation !== audioGeneration || get().currentTrack?.id !== fileId) return
          if (cached && cached.blob) {
            activeBlobUrl = URL.createObjectURL(cached.blob)
            audio.src = activeBlobUrl
            audio.setAttribute('data-track-id', key)
            audio.setAttribute('data-is-cached', 'true')
            usePlayerStore.setState({ isPlayingFromCache: true })
            try {
              audio.load()
            } catch {
              /* ignore */
            }
            if (autoplay && get().isPlaying) {
              triggerPlayback()
            }
          } else {
            fallbackStream()
          }
        }).catch(() => fallbackStream())
      } else {
        fallbackStream()
      }
    } else {
      if (autoplay && get().isPlaying) {
        triggerPlayback()
      }
    }

    if (!autoplay) return
    setMediaPlaybackState('playing')
  }

  /** Resume on next user gesture / tab visible if OS blocked autoplay. */
  function armGlobalResume() {
    if (IS_NATIVE || resumeCleanup || !get().isPlaying || !get().currentTrack || !get().audioElement) return
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
      resumeCleanup = null
    }
    resumeCleanup = cleanup
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
        clearRecovery()
        return
      }
      if (audioElement.paused && audioElement.readyState >= 1) {
        const generation = audioGeneration
        const p = audioElement.play()
        if (p && p.catch) p.catch(() => {
          if (generation === audioGeneration) armGlobalResume()
        })
      }
    }, 1500)
    set({ _watchdogId: id })
  }

  /** Best-effort localStorage snapshot so F5 / WebView reload keeps the queue. */
  let lastPersist = 0
  function persistSession(force = false) {
    // Position updates fire ~4x/sec; the snapshot only needs to be recent,
    // not per-frame. Force it on structural changes (new track, queue edit).
    const now = Date.now()
    if (!force && now - lastPersist < 4000) return
    lastPersist = now
    try {
      const s = get()
      if (!s.currentTrack || !s.queue.length) {
        localStorage.removeItem(SESSION_KEY)
        return
      }
      const queue = s.queue.slice(0, SESSION_QUEUE_CAP).map((t) => ({
        id: t.id,
        name: t.name,
        stream_url: t.stream_url || null,
        folder_id: t.folder_id ?? null,
        mime_type: t.mime_type || null,
        size: t.size ?? null,
        duration: durationsCache[t.id] ?? t.duration ?? null,
      }))
      const payload = {
        queue,
        queueIndex: Math.min(s.queueIndex, queue.length - 1),
        currentRelease: s.currentRelease,
        position: s.currentTime || 0,
        shuffle: s.shuffle,
        repeat: s.repeat,
        savedAt: Date.now(),
      }
      localStorage.setItem(SESSION_KEY, JSON.stringify(payload))
    } catch {
      /* quota / private mode — session restore is best-effort */
    }
  }

  return {
    currentTrack: null,
    currentRelease: null, // { title, artistName, coverUrl }
    queue: [],
    queueIndex: -1,
    isPlaying: false,
    isBuffering: false,
    currentTime: 0,
    duration: 0,
    bufferedUpTo: 0,
    volume: initialVolume,
    isMuted: initialVolume === 0,
    shuffle: false,
    repeat: 'off', // 'off' | 'all' | 'one'
    isPlayerExpanded: false,
    // UI-22: was a single slot — a new message silently overwrote the previous
    // one (e.g. "transcode done" vanished 200ms later when the stream errored).
    // Now a bounded stack so nothing is lost; rendered by <ToastHost>.
    notices: [], // [{ id, kind: 'error' | 'info', text }]
    noticeSeq: 0,
    durations: durationsCache, // fileId -> seconds (learned at runtime)
    audioElement: null,
    isPlayingFromCache: false,
    _watchdogId: null,

    startTranscodeEvents: () => initTranscodeSSE(),
    stopTranscodeEvents: () => stopTranscodeSSE(),
    dispose: () => {
      cleanupBlobUrl()
      if (get().currentTrack && get().queue.length) persistSession(true)
      get().reset({ preserveSession: true })
      set({ audioElement: null, isPlayingFromCache: false })
    },
    setAudioElement: (el) => {
      const previous = get().audioElement
      if (previous && previous !== el) {
        clearRecovery()
        previous.pause?.()
      }
      if (el) {
        el.volume = get().volume
        el.muted = get().isMuted
      }
      set({ audioElement: el })
    },
    setPlayerExpanded: (expanded) => set({ isPlayerExpanded: expanded }),
    setNotice: (notice) => {
      if (!notice) return
      get().pushNotice(notice)
    },
    pushNotice: (notice) => {
      if (!notice?.text) return null
      const seq = (get().noticeSeq || 0) + 1
      const entry = { id: `n${seq}`, kind: notice.kind || 'info', text: notice.text }
      // Cap at 3 visible toasts; drop the oldest (FIFO).
      set({ notices: [...get().notices, entry].slice(-MAX_NOTICES), noticeSeq: seq })
      return entry.id
    },
    dismissNotice: (id) => {
      if (id == null) return set({ notices: [] })
      return set({ notices: get().notices.filter((n) => n.id !== id) })
    },

    /** Audio reported a real duration — remember for lists/queue. */
    rememberDuration: (fileId, seconds) => {
      if (fileId == null || !Number.isFinite(seconds) || seconds <= 0) return
      if (Math.abs((durationsCache[fileId] || 0) - seconds) < 1) return
      durationsCache[fileId] = seconds
      set({ durations: { ...durationsCache } })
      persistDurations()
    },

    setBuffering: (isBuffering) => set({ isBuffering: !!isBuffering }),
    setBufferedUpTo: (seconds) => set({ bufferedUpTo: seconds || 0 }),

    /**
     * Restore queue/player from localStorage on app start (paused, like the
     * industry players do): current track + position come back, playback waits
     * for a user gesture. Returns true when a session was hydrated.
     */
    hydrate: () => {
      const { currentTrack, audioElement } = get()
      if (currentTrack) return false
      let saved = null
      try {
        saved = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null')
      } catch {
        return false
      }
      if (!saved) return false
      if (!Array.isArray(saved.queue) || saved.queue.length === 0) return false
      const idx = Math.max(0, Math.min(saved.queueIndex ?? 0, saved.queue.length - 1))
      const track = saved.queue[idx]
      if (!track?.id) return false

      syncShuffle(saved.queue.length, idx, !!saved.shuffle)
      set({
        queue: saved.queue,
        queueIndex: idx,
        currentTrack: track,
        currentRelease: saved.currentRelease || null,
        isPlaying: false,
        isBuffering: false,
        currentTime: saved.position || 0,
        duration: durationsCache[track.id] || track.duration || 0,
        shuffle: !!saved.shuffle,
        repeat: saved.repeat === 'all' || saved.repeat === 'one' ? saved.repeat : 'off',
      })
      if (audioElement) {
        startAudio(track.id, { forceLoad: true, autoplay: false })
      }
      log.info('session hydrated', { id: track.id, index: idx, queue: saved.queue.length })
      return true
    },

    reset: ({ preserveSession = false, stopEvents = true } = {}) => {
      const audio = get().audioElement
      clearRecovery()
      if (stopEvents) stopTranscodeSSE()
      for (const ctrl of prefetchAborts.values()) {
        try { ctrl.abort() } catch { }
      }
      prefetchAborts.clear()
      precachedTracks.clear()
      shuffleOrder = []
      orderPos = -1
      if (audio) {
        audio.pause()
        audio.removeAttribute('src')
        audio.load()
        audio.removeAttribute('data-track-id')
      }
      setMediaPlaybackState('none')
      if (!preserveSession) {
        try { localStorage.removeItem(SESSION_KEY) } catch { /* ignore */ }
      }
      set({
        currentTrack: null,
        currentRelease: null,
        queue: [],
        queueIndex: -1,
        isPlaying: false,
        isBuffering: false,
        currentTime: 0,
        duration: 0,
        bufferedUpTo: 0,
        _watchdogId: null,
        notices: [],
      })
    },

    playTrack: (track, releaseInfo = null) => {
      if (!track) return
      const { queue, shuffle } = get()
      let idx = queue.findIndex((t) => t.id === track.id)
      if (idx === -1) {
        syncShuffle(1, 0, shuffle)
        set({
          queue: [track],
          queueIndex: 0,
          currentTrack: track,
          currentRelease: releaseInfo,
          isPlaying: true,
          currentTime: 0,
          duration: durationsCache[track.id] || track.duration || 0,
          bufferedUpTo: 0,
        })
      } else {
        syncShuffle(queue.length, idx, shuffle)
        set({
          queueIndex: idx,
          currentTrack: track,
          currentRelease: releaseInfo || get().currentRelease,
          isPlaying: true,
          currentTime: 0,
          duration: durationsCache[track.id] || track.duration || 0,
          bufferedUpTo: 0,
        })
      }
      log.info('playTrack', { id: track.id, title: track.title, artist: track.artist, fresh_queue: idx === -1 })
      startAudio(track.id)
      ensureWatchdog()
      // If this is a single-track play, also trigger prefetch of upcoming tracks
      if (idx !== -1 && get().queue.length > idx + 1) {
        setTimeout(() => get().precacheNextTracks(), 100)
      }
      persistSession()
    },

    playQueue: (tracks, startIndex = 0, releaseInfo = null) => {
      if (!tracks || tracks.length === 0) return
      // New queue: previous prefetches belong to an obsolete queue — abort them.
      for (const ctrl of prefetchAborts.values()) {
        try { ctrl.abort() } catch { /* ignore */ }
      }
      prefetchAborts.clear()
      const current = tracks[startIndex] || tracks[0]
      const idx = tracks[startIndex] ? startIndex : 0
      syncShuffle(tracks.length, idx, get().shuffle)
      set({
        queue: tracks,
        queueIndex: idx,
        currentTrack: current,
        currentRelease: releaseInfo,
        isPlaying: true,
        currentTime: 0,
        duration: durationsCache[current.id] || current.duration || 0,
        bufferedUpTo: 0,
      })
      log.info('playQueue', { count: tracks.length, start_index: idx, id: current?.id, title: current?.title })
      startAudio(current.id, { forceLoad: true })
      ensureWatchdog()
      get().precacheNextTracks()
      persistSession()
    },

    /**
     * Load a queue into the player WITHOUT starting audio: the fullscreen
     * player opens with the album ready (src preloaded, resume position will
     * apply on metadata). Used when tapping an album card on desktop.
     */
    prepareQueue: (tracks, startIndex = 0, releaseInfo = null) => {
      if (!tracks || tracks.length === 0) return
      for (const ctrl of prefetchAborts.values()) {
        try { ctrl.abort() } catch { /* ignore */ }
      }
      prefetchAborts.clear()
      const current = tracks[startIndex] || tracks[0]
      const idx = tracks[startIndex] ? startIndex : 0
      syncShuffle(tracks.length, idx, get().shuffle)
      set({
        queue: tracks,
        queueIndex: idx,
        currentTrack: current,
        currentRelease: releaseInfo,
        isPlaying: false,
        isBuffering: false,
        currentTime: 0,
        duration: durationsCache[current.id] || current.duration || 0,
        bufferedUpTo: 0,
      })
      log.info('prepareQueue', { count: tracks.length, start_index: idx, id: current?.id })
      startAudio(current.id, { forceLoad: true, autoplay: false })
      persistSession()
    },

    /** Spotify-style "Play next": insert right after the current track. */
    playNext: (track) => {
      if (!track) return
      const { queue, currentTrack } = get()
      if (!currentTrack || queue.length === 0) {
        get().playQueue([track], 0, null)
        return
      }
      if (track.id === currentTrack.id) return
      // Remove the scheduled track first: moving a history item shifts the
      // current index. Never confuse the insertion slot with the playing slot.
      const existing = queue.find((t) => t.id === track.id)
      const nextQueue = queue.filter((t) => t.id !== track.id)
      const currentIdx = nextQueue.findIndex((t) => t.id === currentTrack.id)
      if (currentIdx < 0) return
      nextQueue.splice(currentIdx + 1, 0, existing || track)
      syncShuffle(nextQueue.length, currentIdx, get().shuffle)
      if (get().shuffle) {
        const insertedIdx = currentIdx + 1
        shuffleOrder = [currentIdx, insertedIdx,
          ...shuffleOrder.filter((i) => i !== currentIdx && i !== insertedIdx)]
        orderPos = 0
      }
      set({ queue: nextQueue, queueIndex: currentIdx })
      get().precacheNextTracks()
      persistSession()
    },

    /** "Add to queue": append at the end. */
    addToQueue: (track) => {
      if (!track) return
      const { queue, currentTrack } = get()
      if (!currentTrack || queue.length === 0) {
        get().playQueue([track], 0, null)
        return
      }
      if (queue.some((t) => t.id === track.id)) return
      const nextQueue = [...queue, track]
      syncShuffle(nextQueue.length, get().queueIndex, get().shuffle)
      set({ queue: nextQueue })
      get().precacheNextTracks()
      persistSession()
    },

    /** Remove one item; removing the current track advances in place. */
    removeFromQueue: (idx) => {
      const { queue, queueIndex, audioElement } = get()
      if (idx < 0 || idx >= queue.length) return
      const nextQueue = queue.filter((_, i) => i !== idx)
      if (nextQueue.length === 0) {
        get().reset({ stopEvents: false })
        return
      }
      if (idx === queueIndex) {
        const target = Math.min(idx, nextQueue.length - 1)
        const next = nextQueue[target]
        syncShuffle(nextQueue.length, target, get().shuffle)
        set({
          queue: nextQueue,
          queueIndex: target,
          currentTrack: next,
          isPlaying: true,
          currentTime: 0,
          duration: durationsCache[next.id] || next.duration || 0,
          bufferedUpTo: 0,
        })
        startAudio(next.id, { forceLoad: true })
        ensureWatchdog()
      } else {
        const newCur = idx < queueIndex ? queueIndex - 1 : queueIndex
        // Keep the existing shuffle permutation: drop the removed index and
        // close the gap, so the remaining order (and the current track's slot)
        // is untouched.
        shuffleOrder = shuffleOrder.filter((i) => i !== idx).map((i) => (i > idx ? i - 1 : i))
        orderPos = shuffleOrder.indexOf(newCur)
        set({ queue: nextQueue, queueIndex: newCur })
        if (newCur !== queueIndex && audioElement) {
          audioElement.setAttribute('data-track-id', String(nextQueue[newCur]?.id))
        }
      }
      get().precacheNextTracks()
      persistSession()
    },

    /** Drag / arrow reorder inside the queue. */
    moveQueueItem: (from, to) => {
      const { queue, queueIndex } = get()
      if (from === to || from < 0 || to < 0 || from >= queue.length || to >= queue.length) return
      const next = [...queue]
      const [item] = next.splice(from, 1)
      next.splice(to, 0, item)
      let newCur = queueIndex
      if (from === queueIndex) newCur = to
      else if (from < queueIndex && to >= queueIndex) newCur = queueIndex - 1
      else if (from > queueIndex && to <= queueIndex) newCur = queueIndex + 1
      syncShuffle(next.length, newCur, get().shuffle)
      set({ queue: next, queueIndex: newCur })
      persistSession()
    },

    /** Drop everything after the current track (keeps history/resume intact). */
    clearUpcoming: () => {
      const { queue, queueIndex } = get()
      if (queueIndex < 0 || queueIndex >= queue.length) return
      const next = queue.slice(0, queueIndex + 1)
      syncShuffle(next.length, queueIndex, get().shuffle)
      set({ queue: next })
      persistSession()
    },

    togglePlay: () => {
      const { isPlaying, audioElement, currentTrack } = get()
      if (!audioElement || !currentTrack) return
      if (isPlaying && !audioElement.paused) {
        audioElement.pause()
        clearRecovery()
        set({ isPlaying: false })
        setMediaPlaybackState('paused')
        persistSession()
        log.debug('pause', { id: currentTrack.id })
      } else {
        set({ isPlaying: true })
        log.debug('resume', { id: currentTrack.id })
        // Resume existing buffer or re-bind src
        if (!audioElement.getAttribute('data-track-id')) {
          startAudio(currentTrack.id)
        } else {
          setMediaPlaybackState('playing')
          const generation = audioGeneration
          const p = audioElement.play()
          if (p && p.catch) p.catch(() => {
            if (generation === audioGeneration) armGlobalResume()
          })
        }
        ensureWatchdog()
        persistSession()
      }
    },

    /** Index of the next track in the effective playback order (for UI hints). */
    upcomingIndices: () => {
      const { queue, queueIndex, shuffle } = get()
      if (!queue.length) return []
      if (shuffle && shuffleOrder.length === queue.length) {
        const out = []
        for (let p = orderPos + 1; p < shuffleOrder.length; p++) out.push(shuffleOrder[p])
        return out
      }
      const out = []
      for (let i = queueIndex + 1; i < queue.length; i++) out.push(i)
      return out
    },

    nextTrack: (options = {}) => {
      const { queue, queueIndex, repeat, shuffle, audioElement } = get()
      if (queue.length === 0) return

      if (repeat === 'one' && !options.skipCurrent) {
        log.debug('nextTrack → repeat=one restart', { id: queue[queueIndex]?.id })
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
      if (shuffle && queue.length > 1 && shuffleOrder.length === queue.length) {
        let pos = orderPos + 1
        if (pos >= shuffleOrder.length) {
          if (repeat === 'all') {
            shuffleOrder = buildShuffleOrder(queue.length, queueIndex)
            pos = 1
            orderPos = 0
          } else {
            orderPos = shuffleOrder.length
            clearRecovery()
            set({ isPlaying: false })
            setMediaPlaybackState('none')
            persistSession()
            return
          }
        }
        orderPos = pos
        nextIdx = shuffleOrder[pos]
      } else if (shuffle && queue.length > 1) {
        syncShuffle(queue.length, queueIndex, true)
        orderPos = 1
        nextIdx = shuffleOrder[1] ?? queueIndex
      } else {
        nextIdx = queueIndex + 1
        if (nextIdx >= queue.length) {
          if (repeat === 'all') {
            nextIdx = 0
          } else {
            clearRecovery()
            set({ isPlaying: false })
            setMediaPlaybackState('none')
            persistSession()
            return
          }
        }
      }

      const next = queue[nextIdx]
      if (!next) {
        clearRecovery()
        set({ isPlaying: false })
        return
      }

      // Update state + start audio SYNCHRONOUSLY (critical for Android lockscreen).
      const advancedEnd = nextIdx <= queueIndex && !shuffle
      set({
        queueIndex: nextIdx,
        currentTrack: next,
        isPlaying: true,
        currentTime: 0,
        duration: durationsCache[next.id] || next.duration || 0,
        bufferedUpTo: 0,
      })
      log.debug('nextTrack', { id: next.id, title: next.title, index: nextIdx, queue_wrapped: advancedEnd })
      startAudio(next.id, { forceLoad: true })
      ensureWatchdog()
      get().precacheNextTracks()
      persistSession()
    },

    prevTrack: () => {
      const { queue, queueIndex, currentTime, audioElement, shuffle, repeat } = get()
      if (queue.length === 0) return

      if (currentTime > 3 && audioElement) {
        audioElement.currentTime = 0
        set({ currentTime: 0 })
        log.debug('prevTrack → restart current', { id: queue[queueIndex]?.id })
        return
      }

      let prevIdx
      if (shuffle && shuffleOrder.length === queue.length && orderPos >= 0) {
        let pos = orderPos - 1
        if (pos < 0) {
          if (repeat === 'all') {
            pos = shuffleOrder.length - 1
          } else {
            pos = 0
          }
        }
        orderPos = pos
        prevIdx = shuffleOrder[pos]
      } else {
        prevIdx = queueIndex - 1 < 0 ? queue.length - 1 : queueIndex - 1
      }
      const prev = queue[prevIdx]
      if (!prev) return
      log.debug('prevTrack', { id: prev.id, title: prev.title, index: prevIdx })
      set({
        queueIndex: prevIdx,
        currentTrack: prev,
        isPlaying: true,
        currentTime: 0,
        duration: durationsCache[prev.id] || prev.duration || 0,
        bufferedUpTo: 0,
      })
      startAudio(prev.id, { forceLoad: true })
      ensureWatchdog()
      persistSession()
    },

    seek: (seconds) => {
      const { audioElement } = get()
      if (audioElement) {
        audioElement.currentTime = seconds
        set({ currentTime: seconds })
        log.debug('seek', { to: Math.round(seconds) })
      }
    },

    setVolume: (val) => {
      if (!Number.isFinite(val)) return
      const clamped = Math.max(0, Math.min(1, val))
      if (clamped > 0) lastAudibleVolume = clamped
      localStorage.setItem('tmusic_volume', clamped.toString())
      const { audioElement } = get()
      if (audioElement) {
        audioElement.volume = clamped
        audioElement.muted = clamped === 0
      }
      set({ volume: clamped, isMuted: clamped === 0 })
    },

    toggleMute: () => {
      const { isMuted, volume, audioElement } = get()
      if (isMuted) {
        const restored = volume > 0 ? volume : lastAudibleVolume
        if (audioElement) { audioElement.volume = restored; audioElement.muted = false }
        localStorage.setItem('tmusic_volume', restored.toString())
        set({ isMuted: false, volume: restored })
      } else {
        if (volume > 0) lastAudibleVolume = volume
        if (audioElement) audioElement.muted = true
        set({ isMuted: true }) // Keep the user's audible volume.
      }
    },

    toggleShuffle: () => {
      const { shuffle, queue, queueIndex } = get()
      const next = !shuffle
      if (next) {
        syncShuffle(queue.length, queueIndex, true)
      } else {
        shuffleOrder = []
        orderPos = -1
      }
      set({ shuffle: next })
      persistSession()
    },

    cycleRepeat: () => {
      const { repeat } = get()
      const modes = ['off', 'all', 'one']
      const next = modes[(modes.indexOf(repeat) + 1) % modes.length]
      set({ repeat: next })
      persistSession()
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
      persistSession()
    },

    persistSession: () => persistSession(),

    precacheNextTracks: () => {
      const { queue, queueIndex, shuffle } = get()
      if (!queue || queue.length === 0) return

      // Pre-cache the next tracks in the EFFECTIVE order: with shuffle on,
      // queueIndex+1/+2 may never play (and the real successor would starve),
      // so follow the shuffle permutation instead. The third track gets a
      // small chunk — cheap insurance against a Telegram stall between 1 and 2.
      const upcoming = shuffle ? get().upcomingIndices() : null
      const full =
        upcoming && upcoming.length
          ? upcoming.slice(0, 2)
          : [queueIndex + 1, queueIndex + 2]
      const tiny = upcoming && upcoming.length ? upcoming.slice(2, 3) : [queueIndex + 3]
      const plan = [
        ...full.map((i) => ({ i, bytes: PRECACHE_FULL_BYTES })),
        ...tiny.map((i) => ({ i, bytes: PRECACHE_TAIL_BYTES })),
      ]
      const offlineState = getOfflineStoreState()
      for (const { i, bytes } of plan) {
        const track = queue[i]
        if (!track || precachedTracks.has(track.id) || prefetchAborts.has(track.id) || (offlineState && offlineState.cachedTrackIds.has(Number(track.id)))) continue
        rememberPrecached(track.id)

        const ctrl = new AbortController()
        prefetchAborts.set(track.id, ctrl)
        const url = streamApi.getStreamUrl(track.id)
        // Fetch initial chunk with Range header. The body is consumed (into
        // the HTTP cache) so the connection is released instead of dangling.
        // X-Prefetch tells the backend NOT to auto-trigger a transcode: a
        // buffering request must never enqueue jobs (that caused the mass
        // transcode of the whole upcoming window at PLAYING priority).
        fetch(url, {
          headers: { Range: `bytes=0-${bytes - 1}`, 'X-Prefetch': '1' },
          signal: ctrl.signal,
        })
          .then((resp) => {
            if (!resp.ok || resp.status !== 206) {
              precachedTracks.delete(track.id)
              return null
            }
            return resp.arrayBuffer().catch(() => {
              precachedTracks.delete(track.id)
              return null
            })
          })
          .catch(() => {
            precachedTracks.delete(track.id)
          })
          .finally(() => {
            if (prefetchAborts.get(track.id) === ctrl) {
              prefetchAborts.delete(track.id)
            }
          })
      }

      // NOTE: background transcoding of upcoming tracks is intentionally NOT
      // done here. See prefetchNextTranscodes() — it fires only after the
      // current track has actually started playing (App.jsx handlePlaying),
      // and enqueues exactly 2 tracks at PREFETCH priority.
    },

    /**
     * Sliding transcode window: exactly the next 2 tracks in the EFFECTIVE
     * playback order (shuffle-aware), nothing else. Called only after the
     * current track is really playing, so a queue start no longer bulk-
     * transcodes the whole queue — current track (priority 0, via its own
     * playback GET) + these 2 (priority 1), then the backend stops.
     */
    prefetchNextTranscodes: () => {
      const { queue, queueIndex, shuffle } = get()
      if (!queue || queue.length === 0) return

      const upcoming = shuffle ? get().upcomingIndices() : null
      const idxs =
        upcoming && upcoming.length
          ? upcoming.slice(0, 2)
          : [queueIndex + 1, queueIndex + 2]
      const ids = []
      for (const i of idxs) {
        const track = queue[i]
        if (track && track.id != null) ids.push(track.id)
      }
      if (ids.length === 0) return

      // Fire and forget — backend skips non-transcodable and READY tracks.
      streamApi.prefetchTracks(ids).catch((err) => {
        log.debug('Transcode prefetch failed', { error: err.message })
      })
    },
  }
})
