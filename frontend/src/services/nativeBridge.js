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

export function getNativeStorageInfo() {
  const bridge = getNativeBridge()
  if (bridge && bridge.getDeviceStorageInfo) {
    try {
      const raw = bridge.getDeviceStorageInfo()
      return JSON.parse(raw || '{}')
    } catch {
      return null
    }
  }
  return null
}
