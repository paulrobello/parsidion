// lib/scriptRunner.ts
// SEC-003: hook-script resolution and child-env filtering for the parsidion
// pi/omp extension. Extracted from parsidion.ts into a dependency-free module
// so the resolution order and env contract are unit-testable without the
// @mariozechner/* runtime packages.

import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";

export type HookScriptName =
	| "session_start_hook.py"
	| "session_stop_hook.py"
	| "pre_compact_hook.py"
	| "post_compact_hook.py"
	| "subagent_stop_hook.py";

export const SCRIPT_REQUIRED_FILES: HookScriptName[] = [
	"session_start_hook.py",
	"session_stop_hook.py",
	"pre_compact_hook.py",
	"post_compact_hook.py",
	"subagent_stop_hook.py",
];

// SEC-003: the candidate list is deliberately free of cwd-relative entries.
// The pre-fix resolver searched `<cwd>/../parsidion/skills/parsidion/scripts`
// and `<cwd>/../parsidion/scripts` ahead of the installed copy, so cloning any
// untrusted repo named `parsidion` beside a workspace made every pi/omp
// session in sibling projects execute that repo's hook scripts. Repo-local
// development uses PARSIDION_SCRIPTS_DIR (or PARSIDION_DIR) instead — see
// docs/PI_EXTENSION.md.
export function candidateScriptDirs(cwd: string): string[] {
	void cwd; // signature kept for callers; cwd is intentionally unused
	const envScriptDir = process.env.PARSIDION_SCRIPTS_DIR;
	const envRepoDir = process.env.PARSIDION_DIR;
	const dirs = [
		envScriptDir,
		envRepoDir ? path.join(envRepoDir, "skills", "parsidion", "scripts") : undefined,
		path.join(os.homedir(), ".claude", "skills", "parsidion", "scripts"),
	];
	return dirs.filter((dir): dir is string => Boolean(dir));
}

export function resolveScriptDir(cwd: string): string | undefined {
	for (const dir of candidateScriptDirs(cwd)) {
		if (!existsSync(dir)) continue;
		const hasAllFiles = SCRIPT_REQUIRED_FILES.every((file) => existsSync(path.join(dir, file)));
		if (hasAllFiles) return dir;
	}
	return undefined;
}

// SEC-003: env forwarded to spawned hook processes. Mirrors the Python
// `_SAFE_ENV_KEYS` contract (skills/parsidion/scripts/core/vault_hooks.py)
// plus the pi-specific wildcards the hooks read: LC_*/XDG_* locale and
// config dirs, CLAUDE_VAULT, and PARSIDION_* extension variables.
// `CLAUDECODE` is never forwarded (allowlist, plus an explicit delete so a
// future allowlist edit cannot reintroduce Claude's nesting guard trip).
const SAFE_ENV_KEYS: readonly string[] = [
	"PATH",
	"HOME",
	"USER",
	"SHELL",
	"TERM",
	"LANG",
	"LC_ALL",
	"TMPDIR",
	"ANTHROPIC_API_KEY",
	"ANTHROPIC_AUTH_TOKEN",
	"ANTHROPIC_BASE_URL",
	"ANTHROPIC_CUSTOM_HEADERS",
	"ANTHROPIC_DEFAULT_HAIKU_MODEL",
	"ANTHROPIC_DEFAULT_SONNET_MODEL",
	"ANTHROPIC_DEFAULT_OPUS_MODEL",
	"API_TIMEOUT_MS",
	"HTTPS_PROXY",
	"HTTP_PROXY",
	"PARMEM_MCP_URL",
	"CLAUDE_VAULT",
];

const SAFE_ENV_PREFIXES: readonly string[] = ["LC_", "XDG_", "PARSIDION_"];

const SAFE_ENV_SET: ReadonlySet<string> = new Set(SAFE_ENV_KEYS);

export type EnvRecord = Record<string, string | undefined>;

export function buildHookEnv(source: EnvRecord = process.env): NodeJS.ProcessEnv {
	const filtered: EnvRecord = {};
	for (const key of Object.keys(source)) {
		const value = source[key];
		if (value === undefined) continue;
		if (SAFE_ENV_SET.has(key) || SAFE_ENV_PREFIXES.some((prefix) => key.startsWith(prefix))) {
			filtered[key] = value;
		}
	}
	delete filtered.CLAUDECODE;
	return filtered as NodeJS.ProcessEnv;
}
