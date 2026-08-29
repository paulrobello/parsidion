// lib/promptRecall.ts
// Pure helpers for per-prompt vault recall (UserPromptSubmit parity with the
// Claude Code/codex hook registrations). Extracted from parsidion.ts into a
// dependency-free module so the payload and stdout-parse contracts are
// unit-testable without the @mariozechner/* runtime packages.

export type PromptRecallPayload = {
	prompt: string;
	cwd: string;
	session_id?: string;
};

export function shouldRecall(prompt: string): boolean {
	const trimmed = prompt.trim();
	if (!trimmed) return false;
	// "/"-prefixed input is a pi skill/template invocation, not prose a vault
	// query could match.
	return !trimmed.startsWith("/");
}

export function buildRecallPayload(prompt: string, cwd: string, sessionId?: string): PromptRecallPayload {
	const payload: PromptRecallPayload = { prompt, cwd };
	if (sessionId) payload.session_id = sessionId;
	return payload;
}

type HookResponseShape = {
	additionalContext?: unknown;
	hookSpecificOutput?: {
		additionalContext?: unknown;
	} | null;
};

function extractAdditionalContextValue(parsed: unknown): string | undefined {
	if (typeof parsed !== "object" || parsed === null) return undefined;
	const response = parsed as HookResponseShape;
	const specific =
		typeof response.hookSpecificOutput === "object" && response.hookSpecificOutput !== null
			? response.hookSpecificOutput.additionalContext
			: undefined;
	const value = typeof specific === "string" ? specific : response.additionalContext;
	return typeof value === "string" && value.trim() ? value : undefined;
}

// Mirrors parsidion.ts parseHookResponse/extractAdditionalContext (including
// the trailing-{ recovery for stdout with leading diagnostics) but accepts
// unknown so tests can pass parsed objects directly.
export function recallResponseToContent(stdout: unknown): string | undefined {
	if (typeof stdout !== "string") return extractAdditionalContextValue(stdout);
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		return extractAdditionalContextValue(JSON.parse(trimmed));
	} catch {
		const start = trimmed.indexOf("{");
		if (start < 0) return undefined;
		try {
			return extractAdditionalContextValue(JSON.parse(trimmed.slice(start)));
		} catch {
			return undefined;
		}
	}
}
