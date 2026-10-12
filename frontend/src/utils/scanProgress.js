// Presentation helpers for a catalog scan job.
//
// Kept apart from the component so the arithmetic that decides "how full is the
// bar" is testable on its own — it is the one thing a user can actually see go
// wrong (a bar stuck at 0 %, or a fake 100 % before the work is done).

// Stages emitted by `scan_service.ScanJob.progress`.
export const SCAN_STAGE_LABEL = {
  idle: 'Подготовка',
  artist: 'Данные артиста',
  albums: 'Обложки альбомов',
  save: 'Сохранение',
  done: 'Готово',
  failed: 'Ошибка',
}

export const SCAN_TERMINAL_STATUSES = ['completed', 'failed']

/**
 * Percentage of the scan that is done, or `null` when the total is not known
 * yet (the artist lookup phase). `null` is deliberate: rendering "0 %" there
 * would claim the work has not started, and rendering "100 %" would lie.
 */
export function percentOf(progress) {
  const total = Number(progress?.total)
  const current = Number(progress?.current)
  // Garbage in → no bar. A NaN that survived `Number()` must not become a
  // confident "0 %".
  if (!Number.isFinite(total) || !Number.isFinite(current) || total <= 0) return null
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)))
}

/** "3 / 8" for the cover counter, or null when there is nothing to count. */
export function scanCounter(progress) {
  const total = Number(progress?.total) || 0
  const current = Number(progress?.current) || 0
  if (total <= 0) return null
  return { current, total }
}

export function isTerminalScanStatus(status) {
  return SCAN_TERMINAL_STATUSES.includes(status)
}
