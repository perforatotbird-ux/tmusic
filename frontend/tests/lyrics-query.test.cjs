const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const MODULE_PATH = path.join(__dirname, '..', 'src', 'utils', 'lyricsQuery.js')

function loadModule() {
  const code = fs.readFileSync(MODULE_PATH, 'utf8').replace(/^export\s+/gm, '')
  const context = { module: { exports: {} }, console }
  vm.createContext(context)
  vm.runInContext(
    `${code}\n;module.exports = { parseTrackFilename, buildLyricsQuery }`,
    context
  )
  return context.module.exports
}

const { parseTrackFilename, buildLyricsQuery } = loadModule()

// Every filename below is taken verbatim from the live `vfiles` table.
const REAL = [
  '05. Красные Звёзды - Так и будет.mp3',
  '14 Группа  ОРЗ - Сцена коварного заговора.mp3',
  '02. КАЧ&Li$terman - Моя girlfriend.mp3',
  'Vnuk, Vion & Vobula - Вова Петергоф.mp3',
  'Thunderclash - Turtle Power! (TMNT, Metal cover).mp3',
  'TYP808 - Шайка Дурачков 31.tmp.mp3',
  'Battletoads in Battlemaniacs - Going Down.mp3',
  'Макс Петух - Банк 12 (by Axis).mp3',
  '06. Terrorkode Feat. Javi Ssagittar - Sew My Eyes (Cygnosic Remix).mp3',
  '- 01_Kogda_Pridet_Prazdnik(RealTimeMusik2009).mp3',
  'Isolation.mp3',
  '( PaTr1k ) Валентина Петровна Анусова - Техно пранк с Анусовой.mp3',
  '(01) [СБК] Металл доканал соседа.mp3',
  '#1 Моер Волков.mp3',
]

// ─── parseTrackFilename ──────────────────────────────────────────────────────
test('parses "NN. Artist - Title" — the track number precedes the artist', () => {
  const r = parseTrackFilename('05. Красные Звёзды - Так и будет.mp3')
  assert.equal(r.artist, 'Красные Звёзды')
  assert.equal(r.track, 'Так и будет')
})

test('parses "NN Artist - Title" without a dot after the number', () => {
  const r = parseTrackFilename('14 Группа  ОРЗ - Сцена коварного заговора.mp3')
  assert.equal(r.artist, 'Группа ОРЗ')
  assert.equal(r.track, 'Сцена коварного заговора')
})

test('splits on the last separator so a compound artist survives', () => {
  const r = parseTrackFilename('Vnuk, Vion & Vobula - Вова Петергоф.mp3')
  assert.equal(r.artist, 'Vnuk, Vion & Vobula')
  assert.equal(r.track, 'Вова Петергоф')
})

test('keeps a hyphen inside a word without splitting on it', () => {
  const r = parseTrackFilename('08. Gangue Morcego - Dias Escuros.mp3')
  assert.equal(r.artist, 'Gangue Morcego')
  assert.equal(r.track, 'Dias Escuros')
})

test('strips a doubled extension (.tmp.mp3)', () => {
  const r = parseTrackFilename('TYP808 - Шайка Дурачков 31.tmp.mp3')
  assert.equal(r.artist, 'TYP808')
  assert.equal(r.track, 'Шайка Дурачков 31')
})

test('drops bracketed noise from the title but keeps the words around it', () => {
  const r = parseTrackFilename('Thunderclash - Turtle Power! (TMNT, Metal cover).mp3')
  assert.equal(r.artist, 'Thunderclash')
  assert.equal(r.track, 'Turtle Power!')
})

test('handles a leading "- NN_" prefix', () => {
  const r = parseTrackFilename('- 01_Kogda_Pridet_Prazdnik(RealTimeMusik2009).mp3')
  assert.equal(r.artist, null)
  assert.ok(r.track.length > 0)
  assert.ok(!r.track.startsWith('01'))
  assert.ok(!r.track.includes('_'))
})

test('a bare title has no artist rather than a guessed one', () => {
  const r = parseTrackFilename('Isolation.mp3')
  assert.equal(r.artist, null)
  assert.equal(r.track, 'Isolation')
})

test('"NN - Title" is a track number, not an artist', () => {
  const r = parseTrackFilename('09 - For The Punx.mp3')
  assert.equal(r.artist, null)
  assert.equal(r.track, 'For The Punx')
})

test('"#N Title" yields no artist', () => {
  const r = parseTrackFilename('#1 Моер Волков.mp3')
  assert.equal(r.artist, null)
  assert.equal(r.track, 'Моер Волков')
})

test('never returns an empty artist string', () => {
  for (const name of REAL) {
    const r = parseTrackFilename(name)
    assert.notEqual(r.artist, '', `empty artist for ${name}`)
    assert.ok(r.track.length > 0, `empty track for ${name}`)
  }
})

test('survives empty / junk input', () => {
  for (const bad of ['', '   ', null, undefined, '.mp3']) {
    const r = parseTrackFilename(bad)
    assert.ok(typeof r.track === 'string')
    assert.ok(r.artist === null || typeof r.artist === 'string')
  }
})

// ─── buildLyricsQuery ────────────────────────────────────────────────────────
test('the catalog artist overrides a guessed filename artist', () => {
  const q = buildLyricsQuery({
    fileName: '05. Красные Звёзды - Так и будет.mp3',
    releaseArtist: 'Другой Артист',
  })
  assert.equal(q.artist, 'Другой Артист')
  assert.equal(q.track, 'Так и будет')
})

test('falls back to the filename artist when the release has none', () => {
  const q = buildLyricsQuery({ fileName: 'Thunderclash - Turtle Power! (x).mp3' })
  assert.equal(q.artist, 'Thunderclash')
  assert.equal(q.track, 'Turtle Power!')
})

test('placeholder release artists are ignored, not sent upstream', () => {
  for (const placeholder of ['T-Music', 'Недавнее', 'Офлайн кэш', 'Unknown', '', null]) {
    const q = buildLyricsQuery({
      fileName: 'Thunderclash - Turtle Power!.mp3',
      releaseArtist: placeholder,
    })
    assert.equal(q.artist, 'Thunderclash', `placeholder ${placeholder} leaked`)
  }
})

test('drops a duplicated artist prefix from the title half', () => {
  // Filename already contains the artist; the release confirms it. The title
  // must not come out as "Thunderclash - Turtle Power!".
  const q = buildLyricsQuery({
    fileName: 'Thunderclash - Turtle Power!.mp3',
    releaseArtist: 'Thunderclash',
  })
  assert.equal(q.track, 'Turtle Power!')
  assert.ok(!q.track.toLowerCase().startsWith('thunderclash'))
})

test('always sends fileName so the backend can re-parse on a miss', () => {
  const q = buildLyricsQuery({ fileName: 'Isolation.mp3' })
  assert.equal(q.fileName, 'Isolation.mp3')
  assert.equal(q.track, 'Isolation')
})

test('always yields a non-empty track — LRCLIB needs one', () => {
  for (const name of REAL) {
    const q = buildLyricsQuery({ fileName: name })
    assert.ok(q.track && q.track.length > 0, `no track for ${name}`)
  }
})

test('passes album and duration through for exact-duration matching', () => {
  const q = buildLyricsQuery({
    fileName: 'Thunderclash - Turtle Power!.mp3',
    album: 'Reload',
    duration: 231,
  })
  assert.equal(q.album, 'Reload')
  assert.equal(q.duration, 231)
})

test('omits album instead of sending an empty string', () => {
  const q = buildLyricsQuery({ fileName: 'Isolation.mp3', album: '   ' })
  assert.equal(q.album, undefined)
})

test('tolerates being called with no arguments', () => {
  const q = buildLyricsQuery()
  assert.equal(q.artist, null)
  assert.equal(q.track, '')
  assert.equal(q.album, undefined)
})