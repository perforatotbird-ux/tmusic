/**
 * T-Music Service Worker
 *
 * Goals (PWA):
 *  - Instant app shell on repeat visits / flaky network
 *  - Cached cover images (immutable)
 *  - NEVER touch audio streaming (Range/206) or mutating API calls
 *
 * Bump CACHE on deploy of breaking shell changes.
 */
const CACHE = 'tmusic-shell-v1'
const COVER_CACHE = 'tmusic-covers-v1'
const MAX_COVERS = 120
// Hashed asset filenames change every build; without a cap the shell cache
// accumulates a new copy of the whole bundle with each deploy.
const MAX_SHELL = 24

const SHELL_URLS = ['/', '/manifest.json', '/icon.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      // best-effort: offline install still succeeds if one shell URL fails
      await Promise.all(
        SHELL_URLS.map((u) =>
          cache.add(new Request(u, { cache: 'reload' })).catch(() => null)
        )
      )
      await self.skipWaiting()
    })()
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys()
      await Promise.all(
        keys
          .filter((k) => k !== CACHE && k !== COVER_CACHE)
          .map((k) => caches.delete(k))
      )
      await self.clients.claim()
    })()
  )
})

function sameOrigin(url) {
  return url.origin === self.location.origin
}

function isCoverPath(pathname) {
  return pathname === '/api/library/cover' || pathname === '/api/auth/avatar'
}

function isNeverCache(pathname) {
  return (
    pathname.startsWith('/api/stream') ||
    pathname.startsWith('/rest/') ||
    pathname.startsWith('/api/auth') ||
    pathname.startsWith('/api/playback') ||
    pathname.startsWith('/api/admin') ||
    pathname === '/health' ||
    pathname === '/sw.js'
  )
}

async function trimCoverCache() {
  const cache = await caches.open(COVER_CACHE)
  const keys = await cache.keys()
  if (keys.length <= MAX_COVERS) return
  // Delete oldest first (Request order is insertion order in practice).
  const excess = keys.length - MAX_COVERS
  for (let i = 0; i < excess; i++) {
    try {
      await cache.delete(keys[i])
    } catch {
      /* ignore */
    }
  }
}

async function cacheFirstCover(request) {
  const cache = await caches.open(COVER_CACHE)
  const hit = await cache.match(request)
  if (hit) return hit
  const response = await fetch(request)
  if (response && response.ok && response.status === 200) {
    const type = response.headers.get('content-type') || ''
    if (type.startsWith('image/') || type.includes('svg')) {
      try {
        await cache.put(request, response.clone())
        await trimCoverCache()
      } catch {
        /* quota */
      }
    }
  }
  return response
}

async function cacheFirstStatic(request) {
  const cache = await caches.open(CACHE)
  const hit = await cache.match(request)
  if (hit) return hit
  const response = await fetch(request)
  if (response && response.ok && sameOrigin(new URL(request.url))) {
    try {
      await cache.put(request, response.clone())
      await trimShellCache()
    } catch {
      /* ignore */
    }
  }
  return response
}

/** Drop oldest shell/asset entries so repeated deploys don't pile up bundles. */
async function trimShellCache() {
  const cache = await caches.open(CACHE)
  const keys = await cache.keys()
  if (keys.length <= MAX_SHELL) return
  const excess = keys.length - MAX_SHELL
  for (let i = 0; i < excess; i++) {
    // Never evict the navigation shell itself — offline launch depends on it.
    if (keys[i].url.endsWith('/sw.js')) continue
    try {
      await cache.delete(keys[i])
    } catch {
      /* ignore */
    }
  }
}

async function networkFirstNavigation(request) {
  try {
    const response = await fetch(request)
    if (response && response.ok) {
      const cache = await caches.open(CACHE)
      try {
        await cache.put('/', response.clone())
      } catch {
        /* ignore */
      }
    }
    return response
  } catch {
    const cache = await caches.open(CACHE)
    const shell =
      (await cache.match(request)) ||
      (await cache.match('/')) ||
      (await caches.match('/'))
    if (shell) return shell
    return new Response(
      '<!doctype html><meta charset="utf-8"><title>T-Music</title>' +
        '<body style="background:#141210;color:#e7ef5;font-family:sans-serif;padding:2rem">' +
        '<h1>T-Music</h1><p>Нет сети. Откройте приложение позже.</p></body>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    )
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return

  let url
  try {
    url = new URL(request.url)
  } catch {
    return
  }
  if (!sameOrigin(url)) return
  if (isNeverCache(url.pathname)) return

  // SPA navigations → network-first + shell fallback
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request))
    return
  }

  if (isCoverPath(url.pathname)) {
    event.respondWith(cacheFirstCover(request))
    return
  }

  // Hashed build assets are immutable → cache-first
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirstStatic(request))
    return
  }

  // Static public files (icon, etc.)
  if (
    url.pathname === '/icon.svg' ||
    url.pathname === '/manifest.json' ||
    url.pathname === '/favicon.ico'
  ) {
    event.respondWith(cacheFirstStatic(request))
    return
  }

  // Other same-origin GETs (API lists, etc.): go to network, do not cache —
  // data must stay fresh and auth cookies decide visibility.
})
