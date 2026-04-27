import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage } from "@mariozechner/pi-ai";
import {
	type BeforeAgentStartEvent,
	type BeforeAgentStartEventResult,
	type CustomEntry,
	type CustomMessageEntry,
	type ExtensionAPI,
	type ExtensionContext,
	type SessionCompactEvent,
	type SessionEntry,
	type SessionMessageEntry,
	type SessionShutdownEvent,
	type SessionStartEvent,
} from "@mariozechner/pi-coding-agent";
import { Box, Text } from "@mariozechner/pi-tui";

import {
	buildAnthropicStatus,
	formatAnthropicStatusLines,
	readVaultConfigText,
} from "./status";

type HookResult = {
	stdout: string;
	stderr: string;
	code: number;
};

type HookResponse = {
	additionalContext?: string;
	hookSpecificOutput?: {
		hookEventName?: string;
		additionalContext?: string;
	};
};

type HookScriptName =
	| "session_start_hook.py"
	| "session_stop_hook.py"
	| "pre_compact_hook.py"
	| "post_compact_hook.py"
	| "subagent_stop_hook.py";

type PendingContextChunk = {
	source: "startup" | "post_compact";
	text: string;
};

type SubagentResultDetails = {
	id?: string;
	agent?: string;
	status?: string;
	sessionFile?: string;
};

type ProcessedSubagentEntry = {
	id: string;
	agent?: string;
	sessionFile?: string;
	processedAt: number;
};

const EXTENSION_NAME = "parsidion-vault";
const TRANSCRIPT_DIR = path.join(os.homedir(), ".claude", "pi-vault-hooks");
const SCRIPT_REQUIRED_FILES: HookScriptName[] = [
	"session_start_hook.py",
	"session_stop_hook.py",
	"pre_compact_hook.py",
	"post_compact_hook.py",
	"subagent_stop_hook.py",
];
const VAULT_CONTEXT_MESSAGE_TYPE = "parsidion-vault:context";
const SUBAGENT_PROCESSED_ENTRY_TYPE = "parsidion-vault:subagent-processed";
const SUBAGENT_RESULT_MESSAGE_TYPE = "subagent:result";
const HOOK_TIMEOUT_SESSION_START_MS = 12_000;
const HOOK_TIMEOUT_PRE_COMPACT_MS = 8_000;
const HOOK_TIMEOUT_POST_COMPACT_MS = 6_000;
const HOOK_TERMINATE_GRACE_MS = 1_500;
const MAX_VAULT_CONTEXT_CHARS = 12_000;
const STATUS_PREVIEW_MAX_CHARS = 1_800;

const PI_TO_CLAUDE_TOOL_MAP: Record<string, { name: string; mapArgs: (args: Record<string, unknown>) => Record<string, unknown> }> = {
	read: {
		name: "Read",
		mapArgs: (args) => ({
			file_path: typeof args.path === "string" ? args.path : "",
			offset: args.offset,
			limit: args.limit,
		}),
	},
	write: {
		name: "Write",
		mapArgs: (args) => ({
			file_path: typeof args.path === "string" ? args.path : "",
		}),
	},
	edit: {
		name: "Edit",
		mapArgs: (args) => ({
			file_path: typeof args.path === "string" ? args.path : "",
		}),
	},
	grep: {
		name: "Grep",
		mapArgs: (args) => ({
			path: typeof args.path === "string" ? args.path : "",
		}),
	},
	find: {
		name: "Glob",
		mapArgs: (args) => ({
			pattern: typeof args.pattern === "string" ? args.pattern : "",
			path: typeof args.path === "string" ? args.path : "",
		}),
	},
	ls: {
		name: "LS",
		mapArgs: (args) => ({
			path: typeof args.path === "string" ? args.path : "",
		}),
	},
	bash: {
		name: "Bash",
		mapArgs: (args) => ({
			command: typeof args.command === "string" ? args.command : "",
			timeout: args.timeout,
		}),
	},
};

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function asText(content: string | (TextContent | { type: string })[]): string {
	if (typeof content === "string") return content;
	const parts: string[] = [];
	for (const block of content) {
		if (block.type === "text") parts.push(block.text);
	}
	return parts.join("\n");
}

function getSessionKey(ctx: ExtensionContext): string {
	const sessionFile = ctx.sessionManager.getSessionFile() ?? `cwd:${ctx.cwd}`;
	return createHash("sha1").update(sessionFile).digest("hex").slice(0, 16);
}

function getTranscriptPath(ctx: ExtensionContext, suffix?: string): string {
	mkdirSync(TRANSCRIPT_DIR, { recursive: true });
	const base = suffix ? `${getSessionKey(ctx)}-${suffix}` : getSessionKey(ctx);
	return path.join(TRANSCRIPT_DIR, `${base}.jsonl`);
}

function normalizeUserContent(message: UserMessage): string | Array<{ type: "text"; text: string }> {
	if (typeof message.content === "string") return message.content;
	const blocks = message.content
		.filter((block): block is TextContent => block.type === "text")
		.map((block) => ({ type: "text" as const, text: block.text }));
	return blocks.length > 0 ? blocks : "";
}

function normalizeToolCall(block: ToolCall): { type: "tool_use"; name: string; input: Record<string, unknown> } | null {
	const mapping = PI_TO_CLAUDE_TOOL_MAP[block.name];
	if (!mapping) return null;
	return {
		type: "tool_use",
		name: mapping.name,
		input: mapping.mapArgs(block.arguments),
	};
}

function normalizeAssistantContent(
	message: AssistantMessage,
): Array<{ type: "text"; text: string } | { type: "tool_use"; name: string; input: Record<string, unknown> }> {
	const content: Array<{ type: "text"; text: string } | { type: "tool_use"; name: string; input: Record<string, unknown> }> = [];
	for (const block of message.content) {
		if (block.type === "text") {
			content.push({ type: "text", text: block.text });
			continue;
		}
		if (block.type === "toolCall") {
			const toolUse = normalizeToolCall(block);
			if (toolUse) content.push(toolUse);
		}
	}
	return content;
}

function serializeToolResultMessage(message: ToolResultMessage): string | null {
	const text = asText(message.content);
	if (!text.trim()) return null;
	return JSON.stringify({
		type: "user",
		message: {
			content: [{ type: "text", text: `[tool:${message.toolName}]\n${text}` }],
		},
	});
}

function serializeCustomMessage(entry: CustomMessageEntry): string | null {
	const text = asText(entry.content as string | (TextContent | { type: string })[]);
	if (!text.trim()) return null;
	return JSON.stringify({
		type: "user",
		message: {
			content: [{ type: "text", text }],
		},
	});
}

function serializeEntry(entry: SessionEntry): string | null {
	if (entry.type === "custom_message") {
		return serializeCustomMessage(entry as CustomMessageEntry);
	}
	if (entry.type !== "message") return null;

	const messageEntry = entry as SessionMessageEntry;
	const message = messageEntry.message;
	if (message.role === "user") {
		return JSON.stringify({
			type: "user",
			message: {
				content: normalizeUserContent(message),
			},
		});
	}
	if (message.role === "assistant") {
		return JSON.stringify({
			type: "assistant",
			message: {
				content: normalizeAssistantContent(message),
			},
		});
	}
	if (message.role === "toolResult") {
		return serializeToolResultMessage(message);
	}
	return null;
}

function writeSyntheticTranscriptFromEntries(entries: SessionEntry[], transcriptPath: string): string {
	const lines = entries.map((entry) => serializeEntry(entry)).filter((line): line is string => Boolean(line));
	writeFileSync(transcriptPath, `${lines.join("\n")}${lines.length > 0 ? "\n" : ""}`, "utf8");
	return transcriptPath;
}

function writeSyntheticTranscript(ctx: ExtensionContext, suffix?: string): string {
	return writeSyntheticTranscriptFromEntries(ctx.sessionManager.getBranch(), getTranscriptPath(ctx, suffix));
}

function resolveTranscriptPathForHook(ctx: ExtensionContext, suffix?: string): string {
	const sessionFile = ctx.sessionManager.getSessionFile();
	if (sessionFile && existsSync(sessionFile)) return sessionFile;
	return writeSyntheticTranscript(ctx, suffix);
}

function candidateScriptDirs(cwd: string): string[] {
	const envScriptDir = process.env.PARSIDION_SCRIPTS_DIR;
	const envRepoDir = process.env.PARSIDION_DIR;
	const dirs = [
		envScriptDir,
		envRepoDir ? path.join(envRepoDir, "skills", "parsidion", "scripts") : undefined,
		path.resolve(cwd, "../parsidion/skills/parsidion/scripts"),
		path.resolve(cwd, "../parsidion/scripts"),
		path.join(os.homedir(), ".claude", "skills", "parsidion", "scripts"),
	];
	return dirs.filter((dir): dir is string => Boolean(dir));
}

function resolveScriptDir(cwd: string): string | undefined {
	for (const dir of candidateScriptDirs(cwd)) {
		if (!existsSync(dir)) continue;
		const hasAllFiles = SCRIPT_REQUIRED_FILES.every((file) => existsSync(path.join(dir, file)));
		if (hasAllFiles) return dir;
	}
	return undefined;
}

function spawnHookProcess(
	command: string,
	args: string[],
	stdinJson: string,
	options?: { cwd?: string; timeoutMs?: number; terminateGraceMs?: number },
): Promise<HookResult> {
	return new Promise((resolve, reject) => {
		const child = spawn(command, args, {
			cwd: options?.cwd,
			stdio: ["pipe", "pipe", "pipe"],
			env: { ...process.env },
		});

		let stdout = "";
		let stderr = "";
		let settled = false;
		let timeout: NodeJS.Timeout | undefined;
		let forceKill: NodeJS.Timeout | undefined;

		const clearTimers = () => {
			if (timeout) clearTimeout(timeout);
			if (forceKill) clearTimeout(forceKill);
		};

		const finish = (result: HookResult) => {
			if (settled) return;
			settled = true;
			clearTimers();
			resolve(result);
		};

		const fail = (error: unknown) => {
			if (settled) return;
			settled = true;
			clearTimers();
			reject(error);
		};

		child.stdout.on("data", (chunk: Buffer | string) => {
			stdout += chunk.toString();
		});
		child.stderr.on("data", (chunk: Buffer | string) => {
			stderr += chunk.toString();
		});
		child.on("error", (error) => {
			fail(error);
		});
		child.on("close", (code) => {
			finish({ stdout, stderr, code: code ?? -1 });
		});

		if (options?.timeoutMs && options.timeoutMs > 0) {
			timeout = setTimeout(() => {
				stderr += `\n[${EXTENSION_NAME}] hook timed out after ${options.timeoutMs}ms; terminating process`;
				try {
					child.kill("SIGTERM");
				} catch {
					// best effort
				}
				forceKill = setTimeout(() => {
					if (settled) return;
					stderr += `\n[${EXTENSION_NAME}] hook still running after SIGTERM; forcing SIGKILL`;
					try {
						child.kill("SIGKILL");
					} catch {
						finish({ stdout, stderr, code: -1 });
					}
				}, options.terminateGraceMs ?? HOOK_TERMINATE_GRACE_MS);
				forceKill.unref?.();
			}, options.timeoutMs);
			timeout.unref?.();
		}

		child.stdin.on("error", () => {
			// Ignore EPIPE if process exits before consuming stdin.
		});
		child.stdin.end(stdinJson);
	});
}

async function invokeHook(scriptDir: string, scriptName: HookScriptName, payload: Record<string, unknown>, timeoutMs: number): Promise<HookResult> {
	const scriptPath = path.join(scriptDir, scriptName);
	const stdinJson = JSON.stringify(payload);
	const candidates: Array<{ command: string; args: string[] }> = [
		{ command: "uv", args: ["run", "--no-project", scriptPath] },
		{ command: "python3", args: [scriptPath] },
		{ command: "python", args: [scriptPath] },
	];

	let lastError: unknown;
	for (const candidate of candidates) {
		try {
			return await spawnHookProcess(candidate.command, candidate.args, stdinJson, {
				cwd: scriptDir,
				timeoutMs,
				terminateGraceMs: HOOK_TERMINATE_GRACE_MS,
			});
		} catch (error) {
			const code = isRecord(error) ? error.code : undefined;
			if (code === "ENOENT") {
				lastError = error;
				continue;
			}
			throw error;
		}
	}
	throw lastError instanceof Error ? lastError : new Error(`Unable to launch ${scriptName}`);
}

function invokeHookDetached(scriptDir: string, scriptName: HookScriptName, payload: Record<string, unknown>): void {
	const scriptPath = path.join(scriptDir, scriptName);
	const stdinJson = JSON.stringify(payload);
	const candidates: Array<{ command: string; args: string[] }> = [
		{ command: "uv", args: ["run", "--no-project", scriptPath] },
		{ command: "python3", args: [scriptPath] },
		{ command: "python", args: [scriptPath] },
	];

	for (const candidate of candidates) {
		try {
			const child = spawn(candidate.command, candidate.args, {
				cwd: scriptDir,
				detached: true,
				stdio: ["pipe", "ignore", "ignore"],
				env: { ...process.env },
			});
			child.on("error", () => {});
			child.stdin.write(stdinJson);
			child.stdin.end();
			child.unref();
			return;
		} catch (error) {
			const code = isRecord(error) ? error.code : undefined;
			if (code === "ENOENT") continue;
			return;
		}
	}
}

function parseHookResponse(stdout: string): HookResponse | undefined {
	const trimmed = stdout.trim();
	if (!trimmed) return undefined;
	try {
		return JSON.parse(trimmed) as HookResponse;
	} catch {
		const start = trimmed.lastIndexOf("{");
		if (start < 0) return undefined;
		try {
			return JSON.parse(trimmed.slice(start)) as HookResponse;
		} catch {
			return undefined;
		}
	}
}

function extractAdditionalContext(stdout: string): string | undefined {
	const parsed = parseHookResponse(stdout);
	return parsed?.hookSpecificOutput?.additionalContext ?? parsed?.additionalContext;
}

function buildVaultContextMessage(chunks: PendingContextChunk[]): string {
	const parts = chunks.map((chunk) => {
		const title = chunk.source === "startup" ? "SessionStart hook" : "PostCompact hook";
		return `### ${title}\n\n${chunk.text.trim()}`;
	});
	const full = [`Vault inserted the following context:`, "", ...parts].join("\n").trim();
	if (full.length <= MAX_VAULT_CONTEXT_CHARS) return full;
	return `${full.slice(0, MAX_VAULT_CONTEXT_CHARS).trimEnd()}\n\n... [truncated ${full.length - MAX_VAULT_CONTEXT_CHARS} chars]`;
}

function buildStatusPreview(text: string, maxChars = STATUS_PREVIEW_MAX_CHARS): string {
	const trimmed = text.trim();
	if (!trimmed) return "(empty context)";
	if (trimmed.length <= maxChars) return trimmed;
	return `${trimmed.slice(0, maxChars).trimEnd()}\n\n... [truncated ${trimmed.length - maxChars} chars]`;
}

function notify(ctx: ExtensionContext, message: string, level: "info" | "warning" | "error" = "info"): void {
	if (!ctx.hasUI) return;
	ctx.ui.notify(message, level);
}

function reconstructProcessedSubagents(ctx: ExtensionContext): Set<string> {
	const ids = new Set<string>();
	for (const entry of ctx.sessionManager.getEntries()) {
		if (entry.type !== "custom") continue;
		const customEntry = entry as CustomEntry<ProcessedSubagentEntry>;
		if (customEntry.customType !== SUBAGENT_PROCESSED_ENTRY_TYPE) continue;
		const id = customEntry.data?.id;
		if (id) ids.add(id);
	}
	return ids;
}

function findSubagentResultEntries(ctx: ExtensionContext): Array<CustomMessageEntry<SubagentResultDetails>> {
	const found: Array<CustomMessageEntry<SubagentResultDetails>> = [];
	for (const entry of ctx.sessionManager.getEntries()) {
		if (entry.type !== "custom_message") continue;
		const customMessage = entry as CustomMessageEntry<SubagentResultDetails>;
		if (customMessage.customType === SUBAGENT_RESULT_MESSAGE_TYPE) found.push(customMessage);
	}
	return found;
}

async function processSubagentResults(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	scriptDir: string | undefined,
	processedSubagentIds: Set<string>,
): Promise<number> {
	if (!scriptDir) return 0;
	const results = findSubagentResultEntries(ctx);
	let processedCount = 0;

	for (const entry of results) {
		const details = (entry.details ?? {}) as SubagentResultDetails;
		const id = details.id;
		const sessionFile = details.sessionFile;
		if (!id || !sessionFile || processedSubagentIds.has(id)) continue;
		if (!existsSync(sessionFile)) continue;

		invokeHookDetached(scriptDir, "subagent_stop_hook.py", {
			cwd: ctx.cwd,
			agent_id: id,
			agent_type: details.agent ?? "unknown",
			agent_transcript_path: sessionFile,
		});
		processedSubagentIds.add(id);
		pi.appendEntry<ProcessedSubagentEntry>(SUBAGENT_PROCESSED_ENTRY_TYPE, {
			id,
			agent: details.agent,
			sessionFile,
			processedAt: Date.now(),
		});
		processedCount += 1;
	}

	return processedCount;
}

export default function parsidionVaultExtension(pi: ExtensionAPI) {
	const pendingContext: PendingContextChunk[] = [];
	let warnedMissingScripts = false;
	let processedSubagentIds = new Set<string>();

	function registerRenderers() {
		pi.registerMessageRenderer(VAULT_CONTEXT_MESSAGE_TYPE, (message, { expanded }, theme) => {
			const details = isRecord(message.details) ? message.details : {};
			const sources = Array.isArray(details.sources) ? details.sources.map(String) : [];
			const title = theme.fg("accent", theme.bold("Vault context injected"));
			const meta = theme.fg("dim", sources.length > 0 ? `sources: ${sources.join(", ")}` : "sources: vault hooks");
			const lines = String(message.content ?? "").split("\n");
			const body = expanded ? lines.join("\n") : lines.slice(0, 14).join("\n");
			const footer = theme.fg("dim", expanded ? "end of vault context" : "expand for full inserted context");
			const text = [title, meta, "", body, "", footer].join("\n");
			const box = new Box(1, 1, (t) => theme.bg("customMessageBg", t));
			box.addChild(new Text(text, 0, 0));
			return box;
		});
	}

	async function loadStartupContext(event: SessionStartEvent, ctx: ExtensionContext): Promise<void> {
		const scriptDir = resolveScriptDir(ctx.cwd);
		if (!scriptDir) {
			if (!warnedMissingScripts) {
				warnedMissingScripts = true;
				notify(ctx, `${EXTENSION_NAME}: could not find parsidion hook scripts`, "warning");
			}
			return;
		}
		warnedMissingScripts = false;

		try {
			const result = await invokeHook(
				scriptDir,
				"session_start_hook.py",
				{ cwd: ctx.cwd, event: event.reason },
				HOOK_TIMEOUT_SESSION_START_MS,
			);
			const additionalContext = extractAdditionalContext(result.stdout)?.trim();
			if (additionalContext) {
				pendingContext.push({ source: "startup", text: additionalContext });
				const preview = buildStatusPreview(additionalContext);
				notify(
					ctx,
					`${EXTENSION_NAME}: SessionStart hook queued ${additionalContext.length} chars for next turn\n\n${preview}`,
				);
			}
			if (result.code !== 0 && result.stderr.trim()) {
				notify(ctx, `${EXTENSION_NAME}: SessionStart hook exited with code ${result.code}`, "warning");
			}
			void queueSubagentCaptures(ctx, { notifyWhenQueued: true });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify(ctx, `${EXTENSION_NAME}: SessionStart hook failed: ${message}`, "warning");
		}
	}

	async function queueSubagentCaptures(
		ctx: ExtensionContext,
		options?: { notifyWhenQueued?: boolean },
	): Promise<number> {
		const scriptDir = resolveScriptDir(ctx.cwd);
		if (!scriptDir) return 0;
		const subagentCount = await processSubagentResults(pi, ctx, scriptDir, processedSubagentIds);
		if (options?.notifyWhenQueued && subagentCount > 0) {
			notify(ctx, `${EXTENSION_NAME}: queued ${subagentCount} subagent transcript(s) for vault capture`);
		}
		return subagentCount;
	}

	async function runPreCompact(ctx: ExtensionContext): Promise<void> {
		const scriptDir = resolveScriptDir(ctx.cwd);
		if (!scriptDir) return;
		try {
			const transcriptPath = resolveTranscriptPathForHook(ctx, "pre-compact");
			await invokeHook(
				scriptDir,
				"pre_compact_hook.py",
				{ cwd: ctx.cwd, transcript_path: transcriptPath },
				HOOK_TIMEOUT_PRE_COMPACT_MS,
			);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify(ctx, `${EXTENSION_NAME}: PreCompact hook failed: ${message}`, "warning");
		}
	}

	async function runPostCompact(_event: SessionCompactEvent, ctx: ExtensionContext): Promise<void> {
		const scriptDir = resolveScriptDir(ctx.cwd);
		if (!scriptDir) return;
		try {
			const result = await invokeHook(scriptDir, "post_compact_hook.py", { cwd: ctx.cwd }, HOOK_TIMEOUT_POST_COMPACT_MS);
			const additionalContext = extractAdditionalContext(result.stdout)?.trim();
			if (additionalContext) {
				pendingContext.push({ source: "post_compact", text: additionalContext });
				const preview = buildStatusPreview(additionalContext);
				notify(
					ctx,
					`${EXTENSION_NAME}: PostCompact hook queued ${additionalContext.length} chars for next turn\n\n${preview}`,
				);
			}
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify(ctx, `${EXTENSION_NAME}: PostCompact hook failed: ${message}`, "warning");
		}
	}

	function runSessionStopDetached(_event: SessionShutdownEvent, ctx: ExtensionContext): void {
		const scriptDir = resolveScriptDir(ctx.cwd);
		if (!scriptDir) return;
		try {
			const transcriptPath = resolveTranscriptPathForHook(ctx, "session-stop");
			invokeHookDetached(scriptDir, "session_stop_hook.py", {
				cwd: ctx.cwd,
				transcript_path: transcriptPath,
			});
		} catch {
			// shutdown path is best-effort only
		}
	}

	async function consumePendingContext(_event: BeforeAgentStartEvent, _ctx: ExtensionContext): Promise<BeforeAgentStartEventResult | void> {
		if (pendingContext.length === 0) return;

		const chunks = pendingContext.splice(0, pendingContext.length);
		return {
			message: {
				customType: VAULT_CONTEXT_MESSAGE_TYPE,
				content: buildVaultContextMessage(chunks),
				display: true,
				details: { sources: chunks.map((chunk) => chunk.source) },
			},
		};
	}

	registerRenderers();

	pi.on("session_start", async (event, ctx) => {
		pendingContext.length = 0;
		processedSubagentIds = reconstructProcessedSubagents(ctx);
		void loadStartupContext(event, ctx);
	});

	pi.on("before_agent_start", async (event, ctx) => consumePendingContext(event, ctx));

	pi.on("session_before_compact", async (_event, ctx) => {
		await runPreCompact(ctx);
	});

	pi.on("session_compact", async (event, ctx) => {
		await runPostCompact(event, ctx);
	});

	pi.on("turn_end", async (_event, ctx) => {
		void queueSubagentCaptures(ctx).catch(() => {
			// best-effort only
		});
	});

	pi.on("session_shutdown", async (event, ctx) => {
		await queueSubagentCaptures(ctx).catch(() => {
			// best-effort only
		});
		runSessionStopDetached(event, ctx);
	});

	pi.registerCommand("parsidion-vault", {
		description: "Show parsidion vault hook integration status",
		handler: async (_args, ctx) => {
			const scriptDir = resolveScriptDir(ctx.cwd);
			const sessionFile = ctx.sessionManager.getSessionFile();
			const transcriptPath =
				sessionFile && existsSync(sessionFile)
					? sessionFile
					: getTranscriptPath(ctx, "preview");
			const subagentCount = findSubagentResultEntries(ctx).length;
			const configRead = readVaultConfigText(process.env);
			const anthropicStatus = buildAnthropicStatus(process.env, configRead.text);
			const anthropicLines = formatAnthropicStatusLines({
				...anthropicStatus,
				notice: configRead.notice,
			});
			const lines = [
				`extension: ${EXTENSION_NAME}`,
				`scriptDir: ${scriptDir ?? "not found"}`,
				`transcriptPath: ${transcriptPath}`,
				`pendingContextChunks: ${pendingContext.length}`,
				`processedSubagents: ${processedSubagentIds.size}`,
				`visibleSubagentResultsInSession: ${subagentCount}`,
				`sessionFile: ${ctx.sessionManager.getSessionFile() ?? "ephemeral"}`,
				"",
				...anthropicLines,
			];
			notify(ctx, lines.join("\n"), "info");
		},
	});
}
