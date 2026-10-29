/**
 * Shared validation for the admin inline-rename flow (catalog cards, albums and
 * tracks). Kept as a pure function so both the admin panel and the catalog
 * detail views reject blank/unchanged input identically, and so the rule is
 * unit-testable without a DOM.
 */

/**
 * Build the PATCH body for renaming a track, or null when nothing should be
 * sent. `current` is the name the entity has right now — an unchanged draft is
 * a no-op rather than a pointless round-trip.
 *
 * @param {string} draft  text typed by the admin
 * @param {string} current current stored name
 * @returns {{name: string}|null}
 */
export function trackRenamePatch(draft, current) {
  const name = typeof draft === 'string' ? draft.trim() : ''
  if (!name) return null
  if (current != null && name === current) return null
  return { name }
}
