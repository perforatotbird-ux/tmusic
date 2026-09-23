/**
 * LRC (synced lyrics) helpers for the player.
 * Format: [mm:ss.xx] text  — multiple tags per line allowed.
 */

const TIME_TAG = /\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g

function tagToSeconds(min, sec, frac) {
  const m = Number(min) || 0
  const s = Number(sec) || 0
  const fracStr = frac || '0'
  // "5" → 0.5s, "50" → 0.50s, "500" → 0.500s
  const ms =
    fracStr.length === 1
      ? Number(fracStr) * 100
      : fracStr.length === 2
        ? Number(fracStr) * 10
        : Number(fracStr.padEnd(3, '0').slice(0, 3))
  return m * 60 + s + ms / 1000
}

/**
 * Parse raw LRC into sorted [{ time, text }] lines.
 * Lines without timestamps are skipped; empty text lines are kept for spacing.
 */
export function parseLrc(raw) {
  if (!raw || typeof raw !== 'string') return []
  const out = []
  for (const line of raw.split(/\r?\n/)) {
    TIME_TAG.lastIndex = 0
    const tags = []
    let m
    while ((m = TIME_TAG.exec(line)) !== null) {
      tags.push(tagToSeconds(m[1], m[2], m[3]))
    }
    if (!tags.length) continue
    const text = line.replace(TIME_TAG, '').trim()
    for (const time of tags) {
      out.push({ time, text })
    }
  }
  out.sort((a, b) => a.time - b.time)
  return out
}

/** Index of the active line for playback time `t` (-1 if before first). */
export function findLrcIndex(lines, t) {
  if (!lines || !lines.length || t == null || !Number.isFinite(t)) return -1
  let lo = 0
  let hi = lines.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (lines[mid].time <= t + 0.05) {
      ans = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return ans
}

/** Split plain lyrics into display lines. */
export function plainLines(plain) {
  if (!plain) return []
  return plain.split(/\r?\n/)
}
