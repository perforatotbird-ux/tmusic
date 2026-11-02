/**
 * Очередь в полноэкранном плеере — «классический плейлист» (решение владельца
 * 2026-10-29, скриншот с телефона).
 *
 * Было: каждая строка несла кнопки ↑ ↓ 🗑, из-за чего на телефоне от названия
 * трека оставалось ~10 символов, а длительность показывалась только у
 * неиграющих строк. Нужен вид Winamp: позиция → имя → длительность, без
 * управляющих кнопок в строке.
 */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = (name) => fs.readFileSync(path.join(__dirname, '..', 'src', name), 'utf8')

const player = read('components/MusicLibraryPlayer.jsx')
const css = read('index.css')

/** Фрагмент исходника с рендером строк очереди. */
function queueRows() {
  const start = player.indexOf('{rows.map(({ t: track, i: idx })')
  assert.notEqual(start, -1, 'не найден рендер строк очереди')
  return player.slice(start, player.indexOf('Очередь пуста', start))
}

test('в строке очереди нет кнопок управления', () => {
  const rows = queueRows()
  assert.doesNotMatch(rows, /<button/, 'кнопки ↑ ↓ 🗑 должны быть убраны из строки')
  assert.doesNotMatch(rows, /tm-row-actions|tm-queue-actions/)
  assert.doesNotMatch(rows, /removeFromQueue/, 'удаление трека из строки больше не предлагается')
  assert.doesNotMatch(rows, /moveQueueItem/, 'порядок меняется перетаскиванием, не кнопками')
})

test('строка показывает позицию, имя и длительность — в этом порядке', () => {
  const rows = queueRows()
  const pos = rows.indexOf('tm-queue-pos')
  const title = rows.indexOf('tm-queue-row-title')
  const dur = rows.indexOf('tm-queue-dur')
  assert.ok(pos > 0 && title > pos && dur > title, 'порядок колонок: позиция → имя → длительность')
  assert.match(rows, /`\$\{idx \+ 1\}\.`/, 'номер трека как в Winamp — с точкой')
})

test('длительность показывается у каждой строки, включая текущую', () => {
  const rows = queueRows()
  assert.match(rows, /const dur = Number\(durations\[track\.id\] \|\| track\.duration\)/)
  assert.match(rows, /durLabel = Number\.isFinite\(dur\) && dur > 0 \? formatTime\(dur\) : '--:--'/)
  // Раньше условие `!isCurrent` скрывало время у играющего трека.
  assert.doesNotMatch(rows, /\{dur && !isCurrent &&/)
})

test('колонки позиции и времени не «прыгают»: табличные цифры и фиксированная ширина', () => {
  for (const selector of ['.tm-queue-pos', '.tm-queue-dur']) {
    const start = css.indexOf(`${selector} {`)
    assert.notEqual(start, -1, `нет правила ${selector}`)
    const body = css.slice(start, css.indexOf('}', start))
    assert.match(body, /font-variant-numeric:\s*tabular-nums/, `${selector}: цифры должны быть моноширинными`)
    assert.match(body, /width:\s*\d+px/, `${selector}: фиксированная ширина колонки`)
  }
  assert.match(css.slice(css.indexOf('.tm-queue-dur {')), /^[\s\S]{0,200}text-align:\s*right/)
})

test('старые классы кнопок очереди не возвращаются', () => {
  for (const name of ['components/MusicLibraryPlayer.jsx', 'components/FolderBrowser.jsx', 'components/HomeExtraPanels.jsx', 'index.css']) {
    assert.doesNotMatch(read(name), /tm-queue-mini|tm-queue-actions/, `${name}: классы переименованы в tm-row-*`)
  }
  // Кнопки в строках дерева папок и «Недавнего» остались, но под честным именем
  // и с hover/focus-раскрытием для обеих строк.
  assert.match(css, /\.tmusic-tree-row:hover \.tm-row-actions/)
  assert.match(css, /\.tmusic-panel-row:hover \.tm-row-actions/)
})

test('удаление трека из очереди больше не импортируется в плеер', () => {
  assert.doesNotMatch(player, /removeFromQueue/)
  assert.doesNotMatch(player, /\bTrash2\b|\bArrowUp\b|\bArrowDown\b/, 'иконки кнопок тоже не нужны')
})
