/**
 * T-Music Media Session API Bridge
 * Enables system lockscreen controls and track info on iOS, Android, macOS, and Windows.
 */

export function setupMediaSession(playerStore) {
  if (!('mediaSession' in navigator)) return

  const ms = navigator.mediaSession

  ms.setActionHandler('play', () => playerStore.getState().togglePlay())
  ms.setActionHandler('pause', () => playerStore.getState().togglePlay())
  ms.setActionHandler('previoustrack', () => playerStore.getState().prevTrack())
  ms.setActionHandler('nexttrack', () => playerStore.getState().nextTrack())
  ms.setActionHandler('seekto', (details) => {
    if (details.seekTime !== undefined) {
      playerStore.getState().seek(details.seekTime)
    }
  })
}

export function updateMediaSessionMetadata(track, release) {
  if (!('mediaSession' in navigator) || !track) return

  const title = (track.name || 'Без названия').replace(/\.[^/.]+$/, '')
  const artist = release?.artistName || 'T-Music'
  const album = release?.title || ''
  const coverUrl = release?.coverUrl || '/icon.svg'

  navigator.mediaSession.metadata = new MediaMetadata({
    title,
    artist,
    album,
    artwork: [
      { src: coverUrl, sizes: '96x96', type: 'image/jpeg' },
      { src: coverUrl, sizes: '128x128', type: 'image/jpeg' },
      { src: coverUrl, sizes: '192x192', type: 'image/jpeg' },
      { src: coverUrl, sizes: '256x256', type: 'image/jpeg' },
      { src: coverUrl, sizes: '512x512', type: 'image/jpeg' },
    ],
  })
}

export function updateMediaSessionPosition(duration, position) {
  if (!('mediaSession' in navigator) || !duration || isNaN(duration)) return
  try {
    navigator.mediaSession.setPositionState({
      duration: Math.max(0, duration),
      playbackRate: 1,
      position: Math.max(0, Math.min(position, duration)),
    })
  } catch {
    // Ignore position state errors on unsupported browsers
  }
}
