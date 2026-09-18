/**
 * Playback resume + history side-effects for the shared <audio> element.
 */
import { playbackApi } from '../api/playback'
import { usePlayerStore } from '../store/playerStore'

const RESTORE_MIN_SEC = 5
const RESTORE_MAX_RATIO = 0.92
const SAVE_INTERVAL_MS = 5000

const state = {
  trackId: null,
  lastSavedAt: 0,
  historyPosted: false,
  restoreDone: false,
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
  playbackApi
    .addHistory({
      file_id: fileId,
      duration_played_sec: completed ? (Number.isFinite(duration) ? duration : 0) : 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed,
      artist: rel?.artistName || null,
      track: rel?.title || null,
      album: rel?.title || null,
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
    const dur =
      Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration
        : Number(pos?.duration_sec || 0)
    if (offset < RESTORE_MIN_SEC) return
    if (dur > 0 && offset > dur * RESTORE_MAX_RATIO) return
    audio.currentTime = offset
  } catch {
    // unauth / network — ignore
  }
}

export function onTimeUpdate(audio, fileId, currentTime, duration) {
  if (!fileId || !audio) return
  markHistoryStart(fileId, duration, false)

  const now = Date.now()
  if (now - state.lastSavedAt < SAVE_INTERVAL_MS) return
  state.lastSavedAt = now
  playbackApi
    .savePosition(fileId, currentTime, Number.isFinite(duration) ? duration : null)
    .catch(() => {})
}

export function onEnded(fileId, currentTime, duration) {
  if (!fileId) return
  const rel = releaseMeta()
  playbackApi
    .savePosition(fileId, 0, Number.isFinite(duration) ? duration : null)
    .catch(() => {})
  playbackApi
    .addHistory({
      file_id: fileId,
      duration_played_sec: Number.isFinite(currentTime) ? currentTime : 0,
      track_duration_sec: Number.isFinite(duration) ? duration : null,
      completed: true,
      artist: rel?.artistName || null,
      track: rel?.title || null,
      album: rel?.title || null,
    })
    .catch(() => {})
  state.lastSavedAt = Date.now()
}
