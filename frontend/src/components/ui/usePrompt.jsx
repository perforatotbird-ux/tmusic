import { useState, useCallback } from 'react'
import { PromptDialog } from './PromptDialog'

/**
 * UI-38: promise-based replacement for window.prompt.
 *
 *   const { prompt, promptDialog } = usePrompt()
 *   ...
 *   const value = await prompt({ title, message, type: 'password' })
 *   if (value == null) return   // cancelled
 *   ...
 *   return (<>...{promptDialog}</>)
 *
 * Resolves with the entered string, or null when cancelled.
 */
export function usePrompt() {
  const [state, setState] = useState(null)

  const prompt = useCallback((opts) => {
    return new Promise((resolve) => {
      setState({ ...opts, resolve })
    })
  }, [])

  const settle = useCallback((value) => {
    const r = state?.resolve
    setState(null)
    r?.(value)
  }, [state])

  const promptDialog = (
    <PromptDialog
      open={!!state}
      title={state?.title}
      message={state?.message}
      inputLabel={state?.inputLabel}
      placeholder={state?.placeholder}
      defaultValue={state?.defaultValue}
      type={state?.type}
      confirmLabel={state?.confirmLabel}
      cancelLabel={state?.cancelLabel}
      onConfirm={(value) => settle(value)}
      onCancel={() => settle(null)}
    />
  )

  return { prompt, promptDialog }
}
