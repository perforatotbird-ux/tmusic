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

  preparePlayback: async (fileId, { signal } = {}) => {
    signal?.throwIfAborted()
    let result = await request(`/api/stream/${fileId}/transcode`, { method: 'POST', signal })
    for (;;) {
      signal?.throwIfAborted()
      if (result.status === 'ready' || result.status === 'cached') return streamApi.getStreamUrl(fileId, 'mp3')
      if (result.status === 'not_needed') return streamApi.getStreamUrl(fileId)
      if (result.status === 'pending' && result.job_active === false) throw new Error('Подготовка прервана. Нажмите Play для повтора.')
      if (result.status === 'failed') throw new Error('Не удалось подготовить аудио. Нажмите Play для повтора.')
      if (!['pending', 'processing'].includes(result.status)) throw new Error('Неизвестный статус подготовки аудио.')
      await new Promise((resolve, reject) => {
        const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(signal.reason) }
        const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, 2000)
        signal?.addEventListener('abort', abort, { once: true })
        if (signal?.aborted) abort()
      })
      result = await request(`/api/stream/${fileId}/transcode-status`, { signal })
    }
  },

  // Tracklists change only through catalog ops/VFS imports; those call
  // bustMediaCaches(). Single-flight also collapses the duplicate fires from
  // double-clicks and parallel play handlers.
  getFolderTracks: (folderId, recursive = true) => {
    const key = `/api/stream/tracks/${folderId}?recursive=${recursive}`
    return memoize(key, cacheTtl.tracks, () => request(key))
  },

  // Short-lived (10 min) single-purpose token for players that cannot send
  // cookies/headers. Never put the session JWT into ?token= — the backend
  // rejects full session tokens in the query string.
  mintStreamToken: () =>
    request('/api/stream/token', { method: 'POST' }),

  getStreamUrlWithToken: async (fileId) => {
    const { stream_token } = await streamApi.mintStreamToken()
    if (!stream_token || typeof stream_token !== 'string') {
      throw new Error('Stream token unavailable')
    }
    return `/api/stream/${fileId}?token=${encodeURIComponent(stream_token)}`
  },

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
