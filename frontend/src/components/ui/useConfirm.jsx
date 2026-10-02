import { useState, useCallback } from 'react'
import { ConfirmDialog } from './ConfirmDialog'

/**
 * UI-15: promise-based replacement for window.confirm.
 *
 *   const { confirm, confirmDialog } = useConfirm()
 *   ...
 *   const ok = await confirm({ title, message, confirmLabel, danger })
 *   if (!ok) return
 *   // do destructive thing
 *   ...
 *   return (<>...{confirmDialog}</>)
 *
 * Returns a Promise<boolean> so existing control flow (`if (!ok) return`) is
 * preserved, but the prompt is now an accessible modal with focus management.
 */
export function useConfirm() {
  const [state, setState] = useState(null)

  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      setState({ ...opts, resolve })
    })
  }, [])

  const settle = useCallback((value) => {
    const r = state?.resolve
    setState(null)
    r?.(value)
  }, [state])

  const confirmDialog = (
    <ConfirmDialog
      open={!!state}
      title={state?.title}
      message={state?.message}
      confirmLabel={state?.confirmLabel}
      cancelLabel={state?.cancelLabel}
      danger={state?.danger}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  )

  return { confirm, confirmDialog }
}
