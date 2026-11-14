'use client'

import { useCallback, type Dispatch, type SetStateAction } from 'react'

export interface UseKeyboardShortcutsOptions {
  /** Number of currently visible autocomplete rows. */
  itemCount: number
  setSelectedIdx: Dispatch<SetStateAction<number>>
  /** Whether the autocomplete dropdown is currently shown. */
  showDropdown: boolean
  setShowDropdown: Dispatch<SetStateAction<boolean>>
  /** Raw input value — gates Backspace-remove (must be empty). */
  input: string
  values: string[]
  removeLast: () => void
  /**
   * The commit branch (Enter + optional extras). Each call site keeps its
   * own commit semantics; preventDefault stays with the branch that acts.
   */
  handleCommit: (e: React.KeyboardEvent) => void
  /** Additional keys that route to handleCommit alongside Enter. */
  extraCommitKeys?: string[]
}

/**
 * Shared autocomplete chip-input keyboard protocol (ARC-210) — the
 * handleKeyDown previously duplicated between FrontmatterEditor's ChipInput
 * and RelatedInput. ArrowUp/Down move the highlighted row (clamped),
 * Enter/extra keys route to the caller's commit branch, Escape dismisses
 * only the dropdown with stopPropagation (so a parent dialog's window-level
 * Escape handler does not close the whole form), and Backspace on an empty
 * input removes the last chip.
 *
 * Key order, preventDefault/stopPropagation placement, and clamp math are
 * unchanged from both former call sites; extraction is mechanical.
 */
export function useKeyboardShortcuts(options: UseKeyboardShortcutsOptions) {
  const {
    itemCount,
    setSelectedIdx,
    showDropdown,
    setShowDropdown,
    input,
    values,
    removeLast,
    handleCommit,
    extraCommitKeys,
  } = options

  return useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown' && itemCount > 0) {
        e.preventDefault()
        setSelectedIdx(i => Math.min(i + 1, itemCount - 1))
      } else if (e.key === 'ArrowUp' && itemCount > 0) {
        e.preventDefault()
        setSelectedIdx(i => Math.max(i - 1, 0))
      } else if (e.key === 'Enter' || (extraCommitKeys !== undefined && extraCommitKeys.includes(e.key))) {
        handleCommit(e)
      } else if (e.key === 'Escape' && showDropdown) {
        // Only the dropdown is dismissed here — don't let Escape bubble to a
        // parent dialog's window-level handler and close the whole form.
        e.stopPropagation()
        setShowDropdown(false)
      } else if (e.key === 'Backspace' && !input && values.length > 0) {
        e.preventDefault()
        removeLast()
      }
    },
    [itemCount, setSelectedIdx, showDropdown, setShowDropdown, input, values, removeLast, handleCommit, extraCommitKeys],
  )
}
