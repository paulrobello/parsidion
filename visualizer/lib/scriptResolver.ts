// lib/scriptResolver.ts
// Resolves paths under the user's installed skills directory or source repo.
// Server-only: the resolved paths are only meaningful to subprocess-spawning
// server code (searchServer, vaultStatsServer, graph/rebuild route), and the
// resolver itself touches `fs.existsSync`. ARC-041.
import 'server-only'

import path from 'path'
import fs from 'fs'

/**
 * Locate a parsidion script by filename. Resolution order:
 * 1. PARSIDION_SCRIPTS_DIR env override — if set, resolve ONLY there
 *    (no fall-through; deterministic for tests and nonstandard installs).
 * 2. Installed alongside the app: ~/.claude/skills/parsidion/scripts/
 * 3. Source repo: app lives at <repo>/visualizer/, scripts at
 *    <repo>/skills/parsidion/scripts/
 */
export function findParsidionScript(name: string): string | null {
  const override = process.env.PARSIDION_SCRIPTS_DIR
  if (override) {
    const p = path.join(override, name)
    return fs.existsSync(p) ? p : null
  }

  const installed = path.join(
    process.env.HOME || '~',
    '.claude', 'skills', 'parsidion', 'scripts', name
  )
  if (fs.existsSync(installed)) return installed

  const source = path.join(process.cwd(), '..', 'skills', 'parsidion', 'scripts', name)
  if (fs.existsSync(source)) return source

  return null
}
