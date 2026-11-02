// Catalog scans as observable jobs.
//
// «Сканировать» used to be one blocking POST: for tens of seconds the admin saw
// a dead button, which is exactly what «она ничего не выполняет» describes. The
// scan is now a background job on the server (`app/services/scan_service.py`),
// and this store is the single place that starts one, polls it, and keeps the
// snapshot for the top-left progress bar.
//
// Everything hangs off the store rather than a component so the bar survives
// navigating away from the admin panel mid-scan.
import { create } from 'zustand'
import { adminApi } from '../api/admin'
import { startSerialPolling } from '../utils/serialPolling'
import { isTerminalScanStatus } from '../utils/scanProgress'
import { createLogger } from '../utils/logger'

const log = createLogger('scan')

// A finished job stays readable for a while so the admin can read what changed,
// then disappears on its own instead of covering the corner forever.
const KEEP_FINISHED_MS = 15000
const POLL_INTERVAL_MS = 400
// Keep the corner small: newest first, at most a couple of cards.
const MAX_VISIBLE_JOBS = 3

// jobId -> { stop } from startSerialPolling. Module-level on purpose: the
// pollers are a side channel of the store, not UI state.
const pollers = new Map()
// jobId -> timeout id for auto-dismissing a finished job.
const dismissTimers = new Map()

function stopPolling(jobId) {
  const poller = pollers.get(jobId)
  if (poller) {
    poller.stop()
    pollers.delete(jobId)
  }
  const timer = dismissTimers.get(jobId)
  if (timer) {
    clearTimeout(timer)
    dismissTimers.delete(jobId)
  }
}

export const useScanStore = create((set, get) => ({
  jobs: [],

  updateJob: (snapshot) => {
    if (!snapshot?.job_id) return
    const previous = get().jobs.find((job) => job.job_id === snapshot.job_id)
    set((state) => ({
      jobs: state.jobs.map((job) => (
        job.job_id === snapshot.job_id ? { ...job, ...snapshot } : job
      )),
    }))
    const terminal = isTerminalScanStatus(snapshot.status)
    if (terminal && !dismissTimers.has(snapshot.job_id)) {
      stopPolling(snapshot.job_id)
      // The scan wrote to the DB, so anything rendering catalog rows must
      // re-read them. A background job cannot call a component callback.
      if (snapshot.status === 'completed' && !previous?.terminalAnnounced) {
        window.dispatchEvent(new CustomEvent('tmusic:catalog-changed', {
          detail: { type: 'artist', id: snapshot.artist_id },
        }))
        set((state) => ({
          jobs: state.jobs.map((job) => (
            job.job_id === snapshot.job_id ? { ...job, terminalAnnounced: true } : job
          )),
        }))
      }
      const timer = setTimeout(() => get().dismissJob(snapshot.job_id), KEEP_FINISHED_MS)
      dismissTimers.set(snapshot.job_id, timer)
    }
  },

  dismissJob: (jobId) => {
    stopPolling(jobId)
    set((state) => ({ jobs: state.jobs.filter((job) => job.job_id !== jobId) }))
  },

  /**
   * Start a scan and follow it to the end.
   *
   * Resolves to `true` when the request was accepted (the job is now on screen),
   * `false` when the server refused it — e.g. the same artist is already being
   * scanned (409) or too many scans run at once. Either way the reason shows up
   * in the corner, so the admin never has to guess what happened.
   */
  startArtistScan: async (artistId, displayName) => {
    let snapshot = null
    try {
      snapshot = await adminApi.scanArtist(artistId)
    } catch (error) {
      // No job was created; surface the refusal as a card the admin can read.
      get().reportRefusal(artistId, displayName, error?.message || 'Не удалось запустить сканирование.')
      return false
    }
    if (!snapshot?.job_id) {
      get().reportRefusal(artistId, displayName, 'Сервер не вернул задачу сканирования.')
      return false
    }
    set((state) => ({
      jobs: [{ ...snapshot }, ...state.jobs.filter((job) => job.job_id !== snapshot.job_id)]
        .slice(0, MAX_VISIBLE_JOBS),
    }))

    const holder = { poller: null }
    holder.poller = startSerialPolling(
      () => adminApi.getScanJob(snapshot.job_id),
      (status) => get().updateJob(status),
      {
        interval: POLL_INTERVAL_MS,
        onError: (error) => {
          holder.poller?.stop()
          log.warn('scan status polling failed', { jobId: snapshot.job_id, error: error?.message })
          get().updateJob({
            job_id: snapshot.job_id,
            artist_id: artistId,
            display_name: displayName,
            status: 'failed',
            error: 'Не удалось получить статус сканирования.',
          })
        },
      },
    )
    pollers.set(snapshot.job_id, holder.poller)
    return true
  },

  /** A scan that never started still deserves visible feedback. */
  reportRefusal: (artistId, displayName, message) => {
    const jobId = `refused-${artistId}-${Date.now()}`
    set((state) => ({
      jobs: [{
        job_id: jobId,
        artist_id: artistId,
        display_name: displayName,
        status: 'failed',
        error: message,
        report: null,
        progress: { current: 0, total: 0, stage: 'idle', message },
      }, ...state.jobs].slice(0, MAX_VISIBLE_JOBS),
    }))
    const timer = setTimeout(() => get().dismissJob(jobId), KEEP_FINISHED_MS)
    dismissTimers.set(jobId, timer)
  },
}))
