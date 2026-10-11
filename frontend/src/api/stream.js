import { request } from './client'
import { memoize, cacheTtl, bustCache } from './cache'

export const streamApi = {
  // Cookie is sent automatically for same-origin <audio> — no token in URL.
  getStreamUrl: (fileId, representation = 'auto') =>
    `/api/stream/${fileId}${representation === 'mp3' ? '?representation=mp3' : ''}`,

  needsPreparation: (track) => {
    const name = track?.name || ''
    const mime = track?.mime_type || ''
    return /\.(m4a|mp4|ogg|opus|flac|wma|aac)$/i.test(name) ||
      /(?:mp4|m4a|aac|flac|ogg|opus|wma)/i.test(mime)
  },

  // SECURITY: the wait is bounded. A stuck or very slow transcode job used to
  // keep every open tab polling /transcode-status at a fixed 1.5s forever; the
  // budget below is ~25 minutes with backoff, after which the caller gets a
  // retryable error instead of an endless request stream.
  preparePlayback: async (fileId, { signal } = {}) => {
    signal?.throwIfAborted()
    const MAX_POLLS = 180
    const BASE_DELAY_MS = 1500
    const MAX_DELAY_MS = 8000
    let result = await request(`/api/stream/${fileId}/transcode`, { method: 'POST', signal })
    for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
      signal?.throwIfAborted()
      if (result.status === 'ready' || result.status === 'cached') return streamApi.getStreamUrl(fileId, 'mp3')
      if (result.status === 'not_needed') return streamApi.getStreamUrl(fileId)
      if (result.status === 'pending' && result.job_active === false) throw new Error('Подготовка прервана. Нажмите Play для повтора.')
      if (result.status === 'failed') throw new Error('Не удалось подготовить аудио. Нажмите Play для повтора.')
      if (!['pending', 'processing'].includes(result.status)) throw new Error('Неизвестный статус подготовки аудио.')
      await new Promise((resolve, reject) => {
        const win = typeof window !== 'undefined' ? window : null
        let timer = null
        let settled = false
        // No `{ once: true }` here: it would drop the listener on the first
        // event for ANY track, so the fast path would never fire and every
        // iteration would fall back to the timer. Removed explicitly instead.
        const cleanup = () => {
          if (settled) return
          settled = true
          if (timer) clearTimeout(timer)
          signal?.removeEventListener('abort', onAbort)
          win?.removeEventListener('tmusic:transcode-complete', onComplete)
        }
        const onAbort = () => { cleanup(); reject(signal?.reason) }
        const onComplete = (e) => {
          const id = e?.detail?.file_id ?? e?.file_id
          if (id === fileId) {
            cleanup()
            resolve()
          }
        }
        // Backoff + jitter: long jobs poll slower, and many tabs do not retry
        // in lockstep.
        const delay = Math.min(MAX_DELAY_MS, Math.round(BASE_DELAY_MS * Math.pow(1.25, attempt))) + Math.floor(Math.random() * 250)
        timer = setTimeout(() => { cleanup(); resolve() }, delay)
        signal?.addEventListener('abort', onAbort)
        win?.addEventListener('tmusic:transcode-complete', onComplete)
        if (signal?.aborted) onAbort()
      })
      result = await request(`/api/stream/${fileId}/transcode-status`, { signal })
    }
    throw new Error('Подготовка аудио заняла слишком долго. Повторите попытку позже.')
  },

  // Tracklists change only through catalog ops/VFS imports; those call
  // bustMediaCaches(). Single-flight also collapses the duplicate fires from
  // double-clicks and parallel play handlers.
  getFolderTracks: (folderId, recursive = true) => {
    const key = `/api/stream/tracks/${folderId}?recursive=${recursive}`
    return memoize(key, cacheTtl.tracks, () => request(key))
  },

  // SECURITY: no ?token= variant of the stream URL in the SPA. A token in the
  // query string leaks into browser history, Referer and proxy logs. The
  // backend does expose POST /api/stream/token (10 min, scope=stream) for
  // native players that cannot send cookies, but the web player always has the
  // same-origin session cookie, so the mint+query helpers here were dead code
  // and were removed rather than left as a landmine.

  // ─── Transcoding API ────────────────────────────────────────────────────────

  /** SSE events for real-time transcoding notifications */
  createTranscodeEventSource: () => {
    return new EventSource('/api/stream/transcode-events')
  },

  /** Poll transcoding status for a track */
  getTranscodeStatus: (fileId) =>
    request(`/api/stream/${fileId}/transcode-status`),

  /** Trigger immediate transcoding (priority: high/playing) */
  triggerTranscode: (fileId) =>
    request(`/api/stream/${fileId}/transcode`, { method: 'POST' }),

  /** Prefetch multiple tracks for background transcoding */
  prefetchTracks: (trackIds) =>
    request('/api/stream/transcode/prefetch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(trackIds),
    }),

  /** Bust caches after VFS import / transcode-related changes */
  bustMediaCaches: () => bustCache('/api/stream'),
}
