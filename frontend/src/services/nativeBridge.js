/**
 * Native Android wrapper detection and bridge helper.
 * When running inside the T-Music Android APK (Capacitor wrapper),
 * the custom User-Agent contains 'TMusicApp/' and window.AndroidMediaBridge is available.
 */

export const IS_NATIVE =
  typeof navigator !== 'undefined' && /TMusicApp\//i.test(navigator.userAgent)

export function getNativeBridge() {
  if (typeof window !== 'undefined' && window.AndroidMediaBridge) {
    return window.AndroidMediaBridge
  }
  return null
}

export function openServerDiscovery() {
  const bridge = getNativeBridge()
  if (bridge && bridge.openLocalDiscovery) {
    bridge.openLocalDiscovery()
  }
}
