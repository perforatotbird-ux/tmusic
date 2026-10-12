// Human-readable summary of a catalog scan report.
//
// The «Сканировать» action used to refresh the list silently, which made a
// successful scan indistinguishable from a dead button — the user's complaint
// was literally «она ничего не выполняет». The backend now returns a report
// (which fields changed, how many album covers were filled, where the artist
// picture came from), and this turns it into one line of feedback.

const SCAN_FIELD_LABEL = {
  bio: 'био',
  mbid: 'MBID',
  image_url: 'обложка',
  cover_url: 'обложка',
  name: 'название',
  title: 'название',
  year: 'год',
}

// `image_source` values produced by the scan endpoints.
const IMAGE_SOURCE_NOTE = {
  album: ' (обложка взята из альбома)',
  existing: ' (обложка оставлена без изменений)',
}

export function describeScan(report) {
  if (!report || typeof report !== 'object') {
    return 'Сканирование завершено.'
  }
  const changed = (Array.isArray(report.fields) ? report.fields : []).map(
    (field) => SCAN_FIELD_LABEL[field] || field,
  )
  if (report.albums_updated) {
    changed.push(`обложки альбомов: ${report.albums_updated}`)
  }
  if (!changed.length) {
    return 'Last.fm не отдал новых данных — изменений нет.'
  }
  const note = IMAGE_SOURCE_NOTE[report.image_source] || ''
  return `Обновлено: ${changed.join(' · ')}${note}`
}
