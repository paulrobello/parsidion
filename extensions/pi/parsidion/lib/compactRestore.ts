// lib/compactRestore.ts
// Post-compact snapshot restore contract for the pi/omp extension.
// post_compact_hook.py is logging-only (PostCompact stdout never reaches the
// model on Claude Code), so session_compact invokes session_start_hook.py
// with source=compact — the same channel Claude Code uses — and relays the
// returned additionalContext at the next before_agent_start. Extracted from
// parsidion.ts into a dependency-free module so the invocation contract is
// unit-testable without the @mariozechner/* runtime packages.

import type { HookScriptName } from "./scriptRunner";

export const COMPACT_RESTORE_SCRIPT: HookScriptName = "session_start_hook.py";

/** Must track the SessionStart budget (Claude Code registers 60 s for the
 *  same script) — the vault-context build can include an AI selection call. */
export const COMPACT_RESTORE_TIMEOUT_MS = 60_000;

export function buildCompactRestorePayload(cwd: string): Record<string, unknown> {
	return { cwd, source: "compact" };
}
