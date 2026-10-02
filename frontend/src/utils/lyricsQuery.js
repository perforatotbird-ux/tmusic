/**
 * Filename → (artist, track) for the lyrics lookup.
 *
 * LRCLIB matches on clean `artist_name` + `track_name`. The library's real
 * filenames are messy, and the raw name almost never matches upstream:
 *
 *   05. Красные Звёзды - Так и будет.mp3   → track number, then artist
 *   14 Группа  ОРЗ - Сцена коварного заговора.mp3
 *   - 01_Kogda_Pridet_Prazdnik(RealTimeMusik2009).mp3
 *   TYP808 - Шайка Дурачков 31.tmp.mp3     → double extension
 *
 * The previous client split on the LAST " - ", so the first case sent
 * track="Красные Звёзды - Так и будет" and never matched.
 *
 * Strategy, in order:
 *   1. release artist from the catalog wins — it is authoritative;
 *   2. otherwise split "Artist - Title" on the LAST separator;
 *   3. strip a leading track number either way;
 *   4. drop noise: bracketed tags, underscores, trailing "(live)"-style tags.
 */

/** Leading track number: `01`, `01.`, `01 -`, `01_`, `-`, `#1`. */
const TRACK_NUMBER = /^[\s\-–—._#]*\d{1,3}\s*[-–—._)]?\s+/
/** A separator with only a number before it is a track number, not an artist. */
const NUMBERED_PREFIX = /^\s*\d{1,3}\s*[-–—.]?\s*/

/**
 * Strip the audio extension, including doubled ones.
 * `TYP808 - Шайка Дурачков 31.tmp.mp3` → `TYP808 - Шайка Дурачков 31`
 */
function stripExtension(name) {
  return name.replace(/(\.[A-Za-z0-9]{1,5}){1,2}$/, '')
}

/** Remove bracketed noise: `[СБК]`, `(live)`, `(2008)`. */
function stripTags(title) {
  return title
    .replace(/\[[^\]]*\]/g, ' ')
    .replace(/\([^)]*\)/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** `Лунный_Модуль` → `Лунный Модуль`; underscores are word separators here. */
function normalizeSpaces(value) {
  return value.replace(/_/g, ' ').replace(/\s+/g, ' ').trim()
}

/**
 * Split a filename body into artist + track.
 * Returns `{ artist: null, track }` when the name holds no artist.
 */
export function parseTrackFilename(fileName) {
  const raw = normalizeSpaces(stripExtension(String(fileName || '').trim()))
  if (!raw) return { artist: null, track: '' }

  // Strip a leading track number so `05. Artist - Title` becomes `Artist - Title`.
  const body = raw.replace(TRACK_NUMBER, '').trim() || raw

  // Last " - " wins: artists may themselves contain hyphens ("Battletoads in
  // Battlemaniacs - Going Down"), and `Vnuk, Vion & Vobula - Вова Петергоф`
  // must not be cut at an earlier separator.
  const parts = body.split(/\s+[-–—]\s+/)
  if (parts.length < 2) {
    return { artist: null, track: stripTags(body) }
  }

  const track = stripTags(parts.slice(1).join(' - '))
  const artistRaw = parts[0].trim()
  // `01 - Вспомни.mp3` is "track number - title", not "artist - title".
  const artist = NUMBERED_PREFIX.test(artistRaw) || /^\d+$/.test(artistRaw) ? null : stripTags(artistRaw)

  return { artist: artist || null, track }
}

/**
 * Release placeholders the catalog uses for "no real artist". Sending one of
 * these upstream guarantees a miss, so it is treated as absent.
 */
const PLACEHOLDER_ARTISTS = new Set([
  'unknown',
  't-music',
  'tmusic',
  'недавнее',
  'офлайн кэш',
  'сборник',
  'кэш устройства',
])

function isPlaceholderArtist(value) {
  return PLACEHOLDER_ARTISTS.has(String(value || '').trim().toLowerCase())
}

/**
 * Build the LRCLIB query for the currently playing track.
 *
 * @param {object} opts
 * @param {string} opts.fileName   Raw `currentTrack.name`, e.g. "05. Artist - Title.mp3"
 * @param {string} [opts.releaseArtist] `currentRelease.artistName` — wins when present
 * @param {string} [opts.album]
 * @param {number} [opts.duration]
 * @returns {{artist: string|null, track: string, album: string|undefined, fileName: string}}
 */
export function buildLyricsQuery({ fileName, releaseArtist, album, duration } = {}) {
  const parsed = parseTrackFilename(fileName)

  // The catalog knows the artist better than any filename heuristic.
  const release = typeof releaseArtist === 'string' ? releaseArtist.trim() : ''
  const artistUsable = release && !isPlaceholderArtist(release) ? release : null

  let artist = artistUsable || parsed.artist
  let track = parsed.track

  // With a known artist, a filename like "Artist - Title" still carries the
  // artist prefix inside the title half — drop it or upstream never matches.
  if (artistUsable && track) {
    const at = track.toLowerCase().indexOf(`${artistUsable.toLowerCase()} -`)
    if (at === 0) track = track.slice(artistUsable.length + 1).trim()
    else if (artist && track.toLowerCase() === artist.toLowerCase()) track = ''
  }

  // `track` alone is never enough for LRCLIB; keep the filename so the backend
  // can retry its own parse when the client could not resolve an artist.
  return {
    artist,
    track: track || parsed.track || '',
    album: album && album.trim() ? album.trim() : undefined,
    duration,
    fileName,
  }
}