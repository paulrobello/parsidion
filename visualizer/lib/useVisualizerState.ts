// ARC-037: God hook split into three focused slices plus this thin orchestrator.
//
// Previously this module returned a fresh ~55-key object literal every render,
// spanning six concerns (vault selection, note tabs, graph controls, search,
// SSE, betweenness). Every `app/page.tsx` callback with `[state]` in its deps
// was recreated each render, defeating the memoization it was written for, and
// the effect at `app/page.tsx` (the "open the new note once nodeMap contains
// it" guard) ran after every render — saved from infinite-looping only by a
// truthiness check on `pendingOpenStem`.
//
// The split:
//   - useVaultSelection  — persisted selected-vault storage
//   - useNoteTabs        — tabs, content cache, CRUD, view/sidebar/history UI
//   - useGraphControls   — threshold, source, filters, sim settings, stats,
//                          and the deferred betweenness trigger
//
// Betweenness (Brandes algorithm) lives in lib/betweenness.ts; SSE lives in
// lib/useVaultFiles.ts. Both are unchanged by this refactor.
//
// The orchestrator's job is two things neither slice can do alone:
//   1. Compose each slice's `_resetForVaultChange` into the cross-cutting
//      `setSelectedVault` wrapper, so a vault switch clears tabs + cache +
//      neighborhood focus synchronously (before the new vault is committed).
//   2. Return a `useMemo`-stabilized object so `state` as a dep is now stable
//      across renders where no actual state changed — `[state]` deps in
//      `app/page.tsx` no longer over-fire.
//
// Public surface (the 55-ish keys consumers destructure, plus the re-exported
// `GraphStats` / `TabInfo` / `SIM_DEFAULTS`) is unchanged.
'use client'

import { useCallback, useMemo } from 'react'
import type { GraphData } from '@/lib/graph'
import { useVaultSelection } from '@/lib/useVaultSelection'
import { useNoteTabs } from '@/lib/useNoteTabs'
import { useGraphControls, SIM_DEFAULTS } from '@/lib/useGraphControls'

// Re-exported for backward compatibility — HUDPanel imports `GraphStats` from
// here, and TabInfo is part of the module's public surface.
export type { GraphStats } from '@/lib/useGraphControls'
export type { TabInfo } from '@/lib/useNoteTabs'
export { SIM_DEFAULTS } from '@/lib/useGraphControls'

export function useVisualizerState(graphData: GraphData | null) {
  const vault = useVaultSelection()
  const tabs = useNoteTabs({ graphData, selectedVault: vault.selectedVault })
  const controls = useGraphControls({ graphData })

  // Pull `_resetForVaultChange` out of the memoized `tabs` bag before wiring
  // it into useCallback: `tabs` re-creates its identity whenever any of its
  // ~25 fields change, but `_resetForVaultChange` itself is internally stable
  // (its only deps are stable useLocalStorage/useState setters). Reading it
  // through a stable local keeps `setSelectedVault` from churn-driving the
  // orchestrator's useMemo — it should only re-create when `vault` (i.e. the
  // current selectedVault) changes.
  const resetTabsForVaultChange = tabs._resetForVaultChange

  // Cross-cutting cleanup: when the vault actually changes, clear tabs + cache
  // + neighborhood focus BEFORE committing the new vault, so consumers never
  // observe a half-cleared UI.
  const setSelectedVault = useCallback(
    (nextVault: string | null) => {
      if (nextVault !== vault.selectedVault) {
        resetTabsForVaultChange()
      }
      vault.setSelectedVaultInternal(nextVault)
    },
    [vault, resetTabsForVaultChange],
  )

  return useMemo(
    () => ({
      // Vault state
      selectedVault: vault.selectedVault,
      setSelectedVault,
      // Tabs / view / content (from useNoteTabs)
      openTabs: tabs.openTabs,
      activeTab: tabs.activeTab,
      activeNode: tabs.activeNode,
      openNote: tabs.openNote,
      closeTab: tabs.closeTab,
      switchTab: tabs.switchTab,
      viewMode: tabs.viewMode,
      setViewMode: tabs.setViewMode,
      neighborhoodCenter: tabs.neighborhoodCenter,
      setNeighborhoodCenter: tabs.setNeighborhoodCenter,
      historyMode: tabs.historyMode,
      historyNote: tabs.historyNote,
      historyPath: tabs.historyPath,
      openHistory: tabs.openHistory,
      closeHistory: tabs.closeHistory,
      sidebarWidth: tabs.sidebarWidth,
      setSidebarWidth: tabs.setSidebarWidth,
      sidebarCollapsed: tabs.sidebarCollapsed,
      setSidebarCollapsed: tabs.setSidebarCollapsed,
      fetchNoteContent: tabs.fetchNoteContent,
      saveNote: tabs.saveNote,
      deleteNote: tabs.deleteNote,
      createNote: tabs.createNote,
      resolveWikilink: tabs.resolveWikilink,
      nodeMap: tabs.nodeMap,
      invalidateNote: tabs.invalidateNote,
      // Graph controls (from useGraphControls)
      threshold: controls.threshold,
      setThreshold: controls.setThreshold,
      graphSource: controls.graphSource,
      setGraphSource: controls.setGraphSource,
      showOverlayEdges: controls.showOverlayEdges,
      toggleOverlayEdges: controls.toggleOverlayEdges,
      filterNodesBySimilarity: controls.filterNodesBySimilarity,
      toggleFilterNodesBySimilarity: controls.toggleFilterNodesBySimilarity,
      activeTypes: controls.activeTypes,
      handleToggleType: controls.handleToggleType,
      showDaily: controls.showDaily,
      toggleShowDaily: controls.toggleShowDaily,
      hideIsolated: controls.hideIsolated,
      toggleHideIsolated: controls.toggleHideIsolated,
      labelsOnHoverOnly: controls.labelsOnHoverOnly,
      toggleLabelsOnHoverOnly: controls.toggleLabelsOnHoverOnly,
      scalingRatio: controls.scalingRatio,
      setScalingRatio: controls.setScalingRatio,
      gravity: controls.gravity,
      setGravity: controls.setGravity,
      slowDown: controls.slowDown,
      setSlowDown: controls.setSlowDown,
      edgeWeightInfluence: controls.edgeWeightInfluence,
      setEdgeWeightInfluence: controls.setEdgeWeightInfluence,
      startTemperature: controls.startTemperature,
      setStartTemperature: controls.setStartTemperature,
      stopThreshold: controls.stopThreshold,
      setStopThreshold: controls.setStopThreshold,
      isLayoutRunning: controls.isLayoutRunning,
      setIsLayoutRunning: controls.setIsLayoutRunning,
      edgeColorMode: controls.edgeColorMode,
      setEdgeColorMode: controls.setEdgeColorMode,
      edgePruning: controls.edgePruning,
      toggleEdgePruning: controls.toggleEdgePruning,
      edgePruningK: controls.edgePruningK,
      setEdgePruningK: controls.setEdgePruningK,
      nodeSizeMode: controls.nodeSizeMode,
      setNodeSizeMode: controls.setNodeSizeMode,
      nodeColorMode: controls.nodeColorMode,
      setNodeColorMode: controls.setNodeColorMode,
      nodeSizeMap: controls.nodeSizeMap,
      nodeSizeComputing: controls.nodeSizeComputing,
      resetSimSettings: controls.resetSimSettings,
      stats: controls.stats,
      graphStats: controls.graphStats,
      SIM_DEFAULTS,
    }),
    [vault, setSelectedVault, tabs, controls],
  )
}
