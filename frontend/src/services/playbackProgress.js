/**
 * Playback resume + history side-effects for the shared <audio> element.
 */
import { playbackApi } from '../api/playback'
import { usePlayerStore } from '../store/playerStore'
import { createLogger } from '../utils/logger'

const log = createLogger('playback-progress')

const RESTORE_MIN_SEC = 5
const RESTORE_MAX_RATIO = 0.92
const SAVE_INTERVAL_MS = 5000

const state = {
  trackId: null,
  historyId: null,
  generation: 0,
  lastSavedAt: 0,
  historyPosted: false,
  restoreDone: false,
  lastCurrentTime: 0,
  lastDuration: null,
}

function releaseMeta() {
  try {
    return usePlayerStore.getState().currentRelease || null
  } catch {
    return null
  }
}

function markHistoryStart(fileId, duration, completed = false) {
  if (!fileId) return
  if (!completed && state.historyPosted) return
  state.historyPosted = true
  const rel = releaseMeta()
  const generation = state.generation
  playbackApi
    .addHistory({
      history_id: state.historyId,
      file_id: fileId,
      duration_played_sec: completed ? (Number.isFinite(duration) ? duration : 0) : 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed,
      artist: rel?.artistName || null,
      track: rel?.title || null,
      album: rel?.title || null,
    })
    .then((res) => {
      if (generation === state.generation && state.trackId === fileId && res?.id) {
        state.historyId = res.id
      }
    })
    .catch((e) => log.debug('history (track changed) not saved', { error: e?.message }))
}

function flushHistoryUpdate(completed = false) {
  if (!state.trackId || !state.historyPosted) return
  const fileId = state.trackId
  const historyId = state.historyId
  const currentTime = state.lastCurrentTime
  const duration = state.lastDuration
  const rel = releaseMeta()
  playbackApi
    .addHistory({
      history_id: historyId,
      file_id: fileId,
      duration_played_sec: Number.isFinite(currentTime) ? currentTime : 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed,
      artist: rel?.artistName || null,
      track: rel?.title || null,
      album: rel?.title || null,
    })
    .catch((e) => log.debug('history update not saved', { error: e?.message }))
}

export function onTrackChanged(fileId) {
  if (state.trackId && state.trackId !== fileId) {
    flushHistoryUpdate(false)
  }
  state.generation += 1
  state.trackId = fileId
  state.historyId = null
  state.lastSavedAt = 0
  state.historyPosted = false
  state.restoreDone = false
  state.lastCurrentTime = 0
  state.lastDuration = null
}

export async function tryRestorePosition(audio, fileId) {
  if (!audio || !fileId || state.restoreDone || state.trackId !== fileId) return
  const generation = state.generation
  state.restoreDone = true
  try {
    const pos = await playbackApi.getPosition(fileId)
    if (generation !== state.generation || state.trackId !== fileId) return
    const boundId = audio.getAttribute?.('data-track-id')
    if (boundId != null && boundId !== String(fileId)) return
    const offset = Number(pos?.position_sec || 0)
    const dur =
      Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration
        : Number(pos?.duration_sec || 0)
    if (!Number.isFinite(offset) || offset < RESTORE_MIN_SEC) return
    if (dur > 0 && offset > dur * RESTORE_MAX_RATIO) return
    audio.currentTime = offset
  } catch {
    // unauth / network — ignore
  }
}

export function onTimeUpdate(audio, fileId, currentTime, duration) {
  if (!fileId || !audio) return
  state.lastCurrentTime = currentTime
  state.lastDuration = duration
  markHistoryStart(fileId, duration, false)

  const now = Date.now()
  if (now - state.lastSavedAt < SAVE_INTERVAL_MS) return
  state.lastSavedAt = now
  playbackApi
    .savePosition(fileId, currentTime, Number.isFinite(duration) ? duration : null)
    .catch((e) => log.debug('position not saved', { error: e?.message }))
}

export function onPause(fileId, currentTime, duration) {
  if (!fileId) return
  if (Number.isFinite(currentTime)) state.lastCurrentTime = currentTime
  if (Number.isFinite(duration)) state.lastDuration = duration
  flushHistoryUpdate(false)
}

export function onEnded(fileId, currentTime, duration) {
  if (!fileId) return
  const rel = releaseMeta()
  const historyId = state.historyId
  playbackApi
    .savePosition(fileId, 0, Number.isFinite(duration) ? duration : null)
    .catch((e) => log.debug('position reset not saved', { error: e?.message }))
  playbackApi
    .addHistory({
      history_id: historyId,
      file_id: fileId,
      duration_played_sec: Number.isFinite(currentTime) ? currentTime : (Number.isFinite(duration) ? duration : 0),
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed: true,
      artist: rel?.artistName || null,
      track: rel?.title || null,
      album: rel?.title || null,
    })
    .catch((e) => log.debug('history (ended) not saved', { error: e?.message }))
  state.lastSavedAt = Date.now()
  state.historyPosted = false
  state.historyId = null
  state.lastCurrentTime = 0
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('pagehide', () => flushHistoryUpdate(false))
  window.addEventListener('beforeunload', () => flushHistoryUpdate(false))
}
