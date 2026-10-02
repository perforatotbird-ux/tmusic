import { useEffect } from 'react'

/**
 * UI-11: four of seven overlays never locked background scrolling, and the ones
 * that did only set `overflow: hidden` — which iOS Safari ignores. This locks
 * the body the way iOS actually honours (position: fixed + negative offset) and
 * restores both scroll position and any pre-existing inline value on unlock.
 *
 * Safe to nest: a module-level counter keeps the lock until the last owner
 * releases it.
 */
let lockCount = 0
let saved = null

export function useBodyScrollLock(active = true) {
  useEffect(() => {
    if (!active) return undefined
    if (typeof document === 'undefined') return undefined

    const body = document.body
    if (lockCount === 0) {
      const scrollY = window.scrollY || window.pageYOffset || 0
      saved = {
        position: body.style.position,
        top: body.style.top,
        left: body.style.left,
        right: body.style.right,
        overflow: body.style.overflow,
        width: body.style.width,
        scrollY,
      }
      body.style.position = 'fixed'
      body.style.top = `-${scrollY}px`
      body.style.left = '0'
      body.style.right = '0'
      body.style.width = '100%'
      body.style.overflow = 'hidden'
    }
    lockCount += 1

    return () => {
      lockCount -= 1
      if (lockCount > 0 || !saved) return
      const s = saved
      saved = null
      body.style.position = s.position
      body.style.top = s.top
      body.style.left = s.left
      body.style.right = s.right
      body.style.width = s.width
      body.style.overflow = s.overflow
      window.scrollTo(0, s.scrollY)
    }
  }, [active])
}

export default useBodyScrollLock
