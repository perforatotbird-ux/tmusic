/**
 * Playback resume + history side-effects for the shared <audio> element.
 */
import { playbackApi } from '../api/playback'

const RESTORE_MIN_SEC = 5
const RESTORE_MAX_RATIO = 0.92
const SAVE_INTERVAL_MS = 5000

const state = {
  trackId: null,
  lastSavedAt: 0,
  historyPosted: false,
  restoreDone: false,
  pendingSaveTimer: null,
}

function markHistoryStart(fileId, duration) {
  if (state.historyPosted || !fileId) return
  state.historyPosted = true
  playbackApi
    .addHistory({
      file_id: fileId,
      duration_played_sec: 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed: false,
    })
    .catch(() => {})
}

export function onTrackChanged(fileId) {
  state.trackId = fileId
  state.lastSavedAt = 0
  state.historyPosted = false
  state.restoreDone = false
}

export async function tryRestorePosition(audio, fileId) {
  if (!audio || !fileId || state.restoreDone) return
  state.restoreDone = true
  try {
    const pos = await playbackApi.getPosition(fileId)
    const offset = Number(pos?.position_sec || 0)
    const dur = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Number(pos?.duration_sec || 0)
    if (offset < RESTORE_MIN_SEC) return
    if (dur > 0 && offset > dur * RESTORE_MAX_RATIO) return
    audio.currentTime = offset
  } catch {
    // unauth / network — ignore
  }
}

export function onTimeUpdate(audio, fileId, currentTime, duration) {
  if (!fileId || !audio) return
  markHistoryStart(fileId, duration)

  const now = Date.now()
  if (now - state.lastSavedAt < SAVE_INTERVAL_MS) return
  state.lastSavedAt = now
  playbackApi
    .savePosition(fileId, currentTime, Number.isFinite(duration) ? duration : null)
    .catch(() => {})
}

export function onEnded(fileId, currentTime, duration) {
  if (!fileId) return
  playbackApi
    .savePosition(fileId, 0, Number.isFinite(duration) ? duration : null)
    .catch(() => {})
  playbackApi
    .addHistory({
      file_id: fileId,
      duration_played_sec: Number.isFinite(currentTime) ? currentTime : 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed: true,
    })
    .catch(() => {})
  state.lastSavedAt = Date.now()
}
