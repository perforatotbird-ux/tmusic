import React from 'react'

/**
 * UI-04: ten places rendered `<div onClick>` with no role, no tabIndex and no
 * key handler — the only way to open an artist, an album, a search result or a
 * track row was with a mouse. This primitive gives those rows button semantics
 * while keeping the arbitrary markup/layout callers need (a real `<button>`
 * cannot legally contain interactive descendants such as drag handles or
 * nested action buttons, hence role="button" on a div rather than a button).
 */
const ClickableRow = React.forwardRef(function ClickableRow(
  { as: Tag = 'div', onClick, onKeyDown, disabled = false, role = 'button', ...rest },
  ref
) {
  const handleKeyDown = (e) => {
    // Let nested controls (buttons, inputs, selects) handle their own keys.
    // A row[role=button] may legally contain real <button> descendants (fav,
    // menu, drag handles), so we must not double-fire when focus is on one.
    const tag = e.target?.tagName
    const inNested = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON' || e.target?.isContentEditable || e.target?.closest?.('[role="button"],[role="menuitem"],[role="tab"]')
    if (inNested) {
      onKeyDown?.(e)
      return
    }
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      // Space on the row itself must not scroll; Enter must not submit.
      e.preventDefault()
      if (!disabled) onClick?.(e)
    }
    onKeyDown?.(e)
  }

  return (
    <Tag
      ref={ref}
      role={role}
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      onKeyDown={handleKeyDown}
      {...rest}
    />
  )
})

export { ClickableRow }
export default ClickableRow
