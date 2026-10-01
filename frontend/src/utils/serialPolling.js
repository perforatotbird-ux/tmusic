/** One request at a time. stop() also invalidates an already pending reply. */
export function startSerialPolling(fetchStatus, onStatus, { interval = 600, onError = () => {} } = {}) {
  let active = true
  let timer = null
  const stop = () => {
    active = false
    if (timer !== null) clearTimeout(timer)
    timer = null
  }
  const poll = async () => {
    timer = null
    if (!active) return
    try {
      const status = await fetchStatus()
      if (!active) return
      onStatus(status)
      if (status?.status === 'completed' || status?.status === 'failed') stop()
    } catch (error) {
      if (active) {
        try { onError(error) } catch { /* Never create an unhandled rejection. */ }
      }
    } finally {
      if (active) timer = setTimeout(poll, interval)
    }
  }
  timer = setTimeout(poll, interval)
  return { stop }
}
