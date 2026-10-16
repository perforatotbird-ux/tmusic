// Mobile layout audit 2026-10-15 (M-01…M-07): «слайдер звука вниз уехал»,
// «кнопки друг на друга наезжают» на телефонах 320–390px.
//
// Source-contract guards: CSS cannot be executed under node --test, so these
// tests pin the wiring that fixed each finding. If someone removes a hook,
// the layout regresses silently on devices — this file must fail first.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const SRC = path.join(__dirname, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8')

const css = read('index.css')
const player = read('components/MusicLibraryPlayer.jsx')
const album = read('components/AlbumDetail.jsx')
const app = read('App.jsx')
const library = read('components/MusicLibrary.jsx')

// M-01: 12px thumb on a 4px rail must be centered, otherwise Chromium docks it
// to the rail's top edge and the knob hangs below the rail.
test('M-01: volume slider thumb is vertically centered on its rail', () => {
  assert.match(css, /\.tm-volume input\[type="range"\]::-webkit-slider-thumb/)
  assert.match(css, /margin-top:\s*-4px/)
})

// M-02: the mini-player row overflows at ≤380px — the compact slider hides,
// the mute toggle stays.
test('M-02: compact volume slider hides on narrow phones', () => {
  assert.match(css, /@media\s*\(max-width:\s*380px\)[\s\S]*?\.tm-compact-volume input\[type="range"\][\s\S]*?display:\s*none/)
  assert.match(css, /\.tm-volume\s*\{[\s\S]*?flex-shrink:\s*0/)
})

// M-03: queue/lyrics headers may wrap instead of overlapping the slider.
test('M-03: side-panel headers carry the wrapping hook', () => {
  const hits = player.match(/className="tm-queue-head"/g) || []
  assert.equal(hits.length, 2, `expected 2 tm-queue-head headers, found ${hits.length}`)
  assert.match(css, /\.tm-queue-head\s*\{[\s\S]*?flex-wrap:\s*wrap/)
})

// M-04: the offline banner must push the sticky topbar down by its real
// height (it wraps to two lines on narrow phones).
test('M-04: offline banner height drives the topbar offset', () => {
  assert.match(app, /bannerRef/)
  assert.match(app, /--offline-banner-h/)
  assert.match(library, /top:\s*'var\(--offline-banner-h, 0px\)'/)
})

// M-05: the fixed track menu must close on scroll, or it floats over
// unrelated rows after a touch-scroll.
test('M-05: album track menu closes on scroll capture', () => {
  assert.match(album, /addEventListener\('scroll', closeMenus, true\)/)
  assert.match(album, /removeEventListener\('scroll', closeMenus, true\)/)
})

// M-06: track duration hides on phones so the title survives next to three
// 44px coarse buttons.
test('M-06: album-sheet duration hides on phones', () => {
  assert.match(album, /className="tm-track-dur"/)
  assert.match(css, /\.tm-track-dur\s*\{[\s\S]*?display:\s*none/)
})

// M-07: artwork floor + scrollable art column on short (landscape) screens.
test('M-07: now-playing artwork never collapses on short screens', () => {
  assert.match(css, /max\(140px, calc\(100dvh - 380px\)\)/)
  assert.match(css, /max-height:\s*640px\)\s*\{[\s\S]*?\.tmusic-nowplaying-art\s*\{[\s\S]*?overflow-y:\s*auto/)
})

// M-08 (2026-10-16, найдено на живом Android): обложка уезжала влево.
// `align-items: center` центрирует обёртку, но картинка внутри неё — блочный
// бокс с `width: 100%`, урезанный `max-width: min(270px, 68vw, …)`, а такой
// бокс всегда липнет к левому краю. Спасают только auto-поля на самой картинке.
test('M-08: now-playing artwork is centred when max-width caps it', () => {
  const rule = css.match(/\.tmusic-nowplaying-art img\s*\{[^}]*\}/)
  assert.ok(rule, 'нет правила .tmusic-nowplaying-art img')
  assert.match(rule[0], /margin-left:\s*auto/)
  assert.match(rule[0], /margin-right:\s*auto/)
  // Обрезка ширины на телефонах/планшетах — источник бага, она должна остаться.
  assert.match(css, /\.tmusic-nowplaying-art img\s*\{[\s\S]*?max-width:\s*min\(270px, 68vw/)
})
