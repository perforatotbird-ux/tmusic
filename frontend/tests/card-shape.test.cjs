/**
 * Все карточки каталога — квадраты «коробка CD» (решение владельца 2026-10-29).
 *
 * Регрессия: артисты рисовались круглыми аватарами (`border-radius: 50%`,
 * `max-width: 140px`, текст по центру), альбомы — квадратами. В одной сетке
 * соседствовали две разные формы и два разных размера, а на телефоне артисты
 * были мельче альбомов (`minmax(140px)` против двух колонок).
 */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = (name) => fs.readFileSync(path.join(__dirname, '..', 'src', name), 'utf8')

const css = read('index.css')

/** Тело CSS-правила по точному селектору (первое вхождение). */
function rule(selector) {
  const start = css.indexOf(`${selector} {`)
  assert.notEqual(start, -1, `нет правила ${selector}`)
  const open = css.indexOf('{', start)
  const close = css.indexOf('}', open)
  return css.slice(open + 1, close)
}

test('обложки артистов и альбомов — одинаковые квадраты', () => {
  const album = rule('.tmusic-card-cover-wrap')
  const artist = rule('.tmusic-artist-card-avatar-wrap')
  for (const [name, body] of [['альбом', album], ['артист', artist]]) {
    assert.match(body, /aspect-ratio:\s*1 \/ 1/, `${name}: обложка должна быть квадратной`)
    assert.match(body, /border-radius:\s*6px/, `${name}: угол «коробки CD»`)
    assert.match(body, /border:\s*1px solid rgba\(255, 255, 255, 0\.09\)/, `${name}: пластиковый бортик`)
    assert.doesNotMatch(body, /border-radius:\s*50%/, `${name}: круглых обложек быть не должно`)
  }
  // Артист больше не урезается до 140px — карточка занимает всю ячейку сетки.
  assert.doesNotMatch(artist, /max-width/, 'обложка артиста не должна быть уже ячейки')
})

test('карточка артиста выровнена как альбомная: обложка во всю ширину, текст слева', () => {
  const body = rule('.tmusic-artist-card')
  assert.match(body, /align-items:\s*stretch/)
  assert.match(body, /text-align:\s*left/)
  assert.match(body, /padding:\s*0/)
  assert.doesNotMatch(body, /text-align:\s*center/)
})

test('картинка внутри квадрата не скругляется отдельно от обёртки', () => {
  assert.match(rule('.tmusic-artist-card-avatar'), /border-radius:\s*0/)
})

test('на телефоне сетки артистов и альбомов — одни и те же 2 колонки', () => {
  const start = css.indexOf('@media (max-width: 640px)')
  const mobile = css.slice(start, start + 4000)
  const block = mobile.slice(
    mobile.indexOf('.tmusic-grid-albums,'),
    mobile.indexOf('.tmusic-grid-albums,') + 220
  )
  assert.match(block, /\.tmusic-grid-albums,\s*\n\s*\.tmusic-grid-artists\s*\{/)
  assert.match(block, /grid-template-columns:\s*repeat\(2, 1fr\)/)
})

test('на планшете у обеих сеток одинаковый минимум колонки', () => {
  const start = css.indexOf('@media (max-width: 900px)')
  const tablet = css.slice(start, start + 600)
  const albums = tablet.slice(tablet.indexOf('.tmusic-grid-albums {'), tablet.indexOf('.tmusic-grid-artists {'))
  const artists = tablet.slice(tablet.indexOf('.tmusic-grid-artists {'), tablet.indexOf('}', tablet.indexOf('.tmusic-grid-artists {')))
  assert.match(albums, /minmax\(150px, 1fr\)/)
  assert.match(artists, /minmax\(150px, 1fr\)/)
})

test('заглушка без обложки тоже квадратная, без ветки «круг для артиста»', () => {
  const src = read('components/MusicLibrary.jsx')
  const start = src.indexOf('function Fallback(')
  const body = src.slice(start, src.indexOf('function ImgWithFallback('))
  assert.doesNotMatch(body, /isCircle/, 'форма больше не зависит от kind')
  assert.match(body, /borderRadius: '6px'/)
  assert.doesNotMatch(src, /kind="artist"/, 'проп kind у ImgWithFallback больше не нужен')
  assert.doesNotMatch(src, /kind="album"/)
})

test('карточка артиста в поиске — тот же квадрат, а не аватар-круг', () => {
  const src = read('components/search/SearchArtistCard.jsx')
  const start = src.indexOf('width: \'76px\'')
  const body = src.slice(start, start + 400)
  assert.match(body, /borderRadius: '6px'/)
  assert.doesNotMatch(body, /borderRadius: '50%'/)
})
