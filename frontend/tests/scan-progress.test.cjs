// The scan progress bar: «Должен быть всплывающий в левом верхнем углу Progress
// bar со статусом получения обложек».
//
// Two halves are covered here:
//   * the arithmetic in `utils/scanProgress.js` (executed, not pattern-matched);
//   * the wiring that puts the bar on screen at all — a source-contract guard,
//     because the wiring is what broke before (a blocking request with no
//     feedback read as «Сканировать ничего не выполняет»).
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const SRC = path.join(__dirname, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf8')

function loadHelpers() {
  const code = read('utils/scanProgress.js').replace(/^export /gm, '')
  const context = {}
  vm.createContext(context)
  vm.runInContext(`${code}\n;globalThis.h = { SCAN_STAGE_LABEL, percentOf, scanCounter, isTerminalScanStatus }`, context)
  return context.h
}

// ── How full is the bar? ────────────────────────────────────────────────────

test('the bar reports a percentage only when the total is known', () => {
  const { percentOf } = loadHelpers()
  // The artist lookup happens before albums are counted: no total yet.
  assert.equal(percentOf({ current: 0, total: 0, stage: 'artist' }), null)
  assert.equal(percentOf(null), null)
  assert.equal(percentOf(undefined), null)
})

test('the bar tracks the cover counter it is given', () => {
  const { percentOf } = loadHelpers()
  assert.equal(percentOf({ current: 0, total: 8, stage: 'albums' }), 0)
  assert.equal(percentOf({ current: 3, total: 8, stage: 'albums' }), 38)
  assert.equal(percentOf({ current: 8, total: 8, stage: 'albums' }), 100)
  assert.equal(percentOf({ current: 12, total: 8, stage: 'albums' }), 100)
})

test('a malformed progress payload never produces a percentage', () => {
  const { percentOf } = loadHelpers()
  assert.equal(percentOf({ current: 'x', total: 8 }), null)
  assert.equal(percentOf({ current: 1, total: null }), null)
  assert.equal(percentOf({ current: 1, total: 'eight' }), null)
})

test('the cover counter is shown only once there is something to count', () => {
  const { scanCounter } = loadHelpers()
  // Objects come from a separate VM realm, so compare fields rather than prototypes.
  const counter = scanCounter({ current: 2, total: 5 })
  assert.equal(counter.current, 2)
  assert.equal(counter.total, 5)
  assert.equal(scanCounter({ current: 0, total: 0 }), null)
  assert.equal(scanCounter(undefined), null)
})

test('terminal statuses are the only ones that stop the polling', () => {
  const { isTerminalScanStatus } = loadHelpers()
  assert.equal(isTerminalScanStatus('running'), false)
  assert.equal(isTerminalScanStatus('completed'), true)
  assert.equal(isTerminalScanStatus('failed'), true)
  assert.equal(isTerminalScanStatus('idle'), false)
})

// ── Wiring: the bar must actually be on screen ───────────────────────────────

test('the progress host is hosted at the app level and anchored top-left', () => {
  const host = read('components/ScanProgressHost.jsx')
  assert.match(host, /position: 'fixed'/)
  assert.match(host, /top: 'calc\(12px \+ var\(--safe-top\) \+ var\(--offline-banner-h, 0px\)\)'/)
  assert.match(host, /left: 'max\(12px, var\(--safe-left\)\)'/)
  // Above the player and nav, below modals and toasts.
  assert.match(host, /zIndex: 'var\(--z-scan\)'/)
  // Must never swallow clicks meant for the UI underneath.
  assert.match(host, /pointerEvents: 'none'/)
  assert.match(host, /pointerEvents: 'auto'/)
})

test('the host renders an accessible progressbar per running job', () => {
  const host = read('components/ScanProgressHost.jsx')
  assert.match(host, /role="progressbar"/)
  assert.match(host, /aria-label=\{`Прогресс сканирования/)
  assert.match(host, /aria-valuemin=\{0\}/)
  assert.match(host, /aria-valuenow=\{percent === null \? undefined : percent\}/)
  // Announce state changes for screen readers while the bar moves.
  assert.match(host, /role="status"/)
  assert.match(host, /aria-live="polite"/)
})

test('the host shows the stage message and the cover counter', () => {
  const host = read('components/ScanProgressHost.jsx')
  assert.match(host, /<Counter progress=\{progress\} \/>/)
  assert.match(host, /const message = progress\.message/)
  // The finished report is what tells the admin the scan actually did something.
  assert.match(host, /describeScan\(job\.report\)/)
  assert.match(host, /job\.error/)
})

test('an unknown total renders an indeterminate stripe, not a fake percentage', () => {
  const host = read('components/ScanProgressHost.jsx')
  assert.match(host, /percent === null \? \(/)
  assert.match(host, /tmusic-scan-indeterminate/)
  assert.ok(read('index.css').includes('@keyframes tmusic-scan-slide'))
})

test('App renders the host so the bar survives navigating away', () => {
  const app = read('App.jsx')
  assert.match(app, /import \{ ScanProgressHost \} from '\.\/components\/ScanProgressHost'/)
  assert.match(app, /<ScanProgressHost \/>/)
})

test('the scan button starts a job instead of awaiting the whole scan', () => {
  const src = read('components/AdminCatalog.jsx')
  assert.match(
    src,
    /useScanStore\.getState\(\)\.startArtistScan\(artist\.id, artist\.display_name\)/,
    'Сканировать must go through the job store — a blocking POST is what read as dead',
  )
  // The old inline await of the scan result is gone.
  assert.doesNotMatch(src, /await adminApi\.scanArtist\(artist\.id\)/)
})

test('the store starts the job, polls it and announces the result once', () => {
  const store = read('store/scanStore.js')
  assert.match(store, /adminApi\.scanArtist\(artistId\)/)
  assert.match(store, /startSerialPolling\(/)
  assert.match(store, /adminApi\.getScanJob\(snapshot\.job_id\)/)
  // A background job cannot call a component callback, so it re-uses the same
  // broadcast channel as a cover change to refresh open lists.
  assert.match(store, /new CustomEvent\('tmusic:catalog-changed'/)
  assert.match(store, /terminalAnnounced: true/)
  // A refused job (409 duplicate / too many scans) must surface too.
  assert.match(store, /reportRefusal\(artistId, displayName, error\?\.message/)
})

test('the scan job endpoint is pollable, not the scan itself', () => {
  const api = read('api/admin.js')
  assert.match(api, /getScanJob: \(jobId\) => request\(`\/api\/admin\/catalog\/scan\/\$\{encodeURIComponent\(jobId\)\}`\)/)
  assert.match(api, /\/\/ The scan itself is a background job/)
})
