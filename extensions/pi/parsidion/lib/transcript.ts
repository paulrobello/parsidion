// lib/transcript.ts
// Serialization of pi SessionEntry branches into Claude-Code-format transcript
// lines (the JSONL shape session_stop_hook.py / agent_adapter.py parse).
// Extracted from parsidion.ts into a dependency-free module so the entry-type
// mapping is unit-testable with bun:test without the @mariozechner/* runtime
// packages — same pattern as lib/scriptRunner.ts.
//
// The structural types below mirror the subset of pi's SessionEntry /
// AgentMessage shapes these functions read. When pi grows its SessionEntry
// union, lib/transcript.test.ts pins the mapping: every content-bearing
// entry type must serialize; the regression suite fails loudly instead of
// silently yielding empty transcripts (kanban 01a0463a99467e619bd092bded7671cb).

export type TextContentLike = { type: "text"; text: string };

export type ContentBlockLike = { type: string; text?: string; name?: string; arguments?: Record<string, unknown> };

export type UserMessageLike = {
	role: "user";
	content: string | ContentBlockLike[];
};

export type AssistantMessageLike = {
	role: "assistant";
	content: ContentBlockLike[];
};

export type ToolResultMessageLike = {
	role: "toolResult";
	toolName: string;
	content: string | ContentBlockLike[];
};

export type CustomMessageEntryLike = {
	type: "custom_message";
	content: string | ContentBlockLike[];
};

export type SessionEntryLike = {
	type: string;
	message?: { role: string; content: unknown; toolName?: string };
	content?: unknown;
	[key: string]: unknown;
};

export const PI_TO_CLAUDE_TOOL_MAP: Record<
	string,
	{ name: string; mapArgs: (args: Record<string, unknown>) => Record<string, unknown> }
> = {
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

export function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

export function asText(content: string | ContentBlockLike[]): string {
	if (typeof content === "string") return content;
	const parts: string[] = [];
	for (const block of content) {
		if (block.type === "text") parts.push(block.text ?? "");
	}
	return parts.join("\n");
}

export function normalizeUserContent(message: UserMessageLike): string | Array<{ type: "text"; text: string }> {
	if (typeof message.content === "string") return message.content;
	const blocks = message.content
		.filter((block): block is TextContentLike => block.type === "text")
		.map((block) => ({ type: "text" as const, text: block.text ?? "" }));
	return blocks.length > 0 ? blocks : "";
}

export function normalizeToolCall(
	block: ContentBlockLike,
): { type: "tool_use"; name: string; input: Record<string, unknown> } | null {
	const mapping = PI_TO_CLAUDE_TOOL_MAP[block.name ?? ""];
	if (!mapping) return null;
	return {
		type: "tool_use",
		name: mapping.name,
		input: mapping.mapArgs(block.arguments ?? {}),
	};
}

export function normalizeAssistantContent(
	message: AssistantMessageLike,
): Array<{ type: "text"; text: string } | { type: "tool_use"; name: string; input: Record<string, unknown> }> {
	const content: Array<
		{ type: "text"; text: string } | { type: "tool_use"; name: string; input: Record<string, unknown> }
	> = [];
	for (const block of message.content) {
		if (block.type === "text") {
			content.push({ type: "text", text: block.text ?? "" });
			continue;
		}
		if (block.type === "toolCall") {
			const toolUse = normalizeToolCall(block);
			if (toolUse) content.push(toolUse);
		}
	}
	return content;
}

export function serializeToolResultMessage(message: ToolResultMessageLike): string | null {
	const text = asText(message.content);
	if (!text.trim()) return null;
	return JSON.stringify({
		type: "user",
		message: {
			content: [{ type: "text", text: `[tool:${message.toolName}]\n${text}` }],
		},
	});
}

export function serializeCustomMessage(entry: CustomMessageEntryLike): string | null {
	const text = asText(entry.content as string | ContentBlockLike[]);
	if (!text.trim()) return null;
	return JSON.stringify({
		type: "user",
		message: {
			content: [{ type: "text", text }],
		},
	});
}

/** Serialize one session entry to a CC-format transcript line, or null when
 * the entry carries no conversation content (control entries, empty texts). */
export function serializeEntry(entry: SessionEntryLike): string | null {
	if (entry.type === "custom_message") {
		return serializeCustomMessage(entry as CustomMessageEntryLike);
	}
	if (entry.type === "compaction") {
		// The compaction entry REPLACES the conversation it summarizes on the
		// branch; dropping its summary would erase everything before it from
		// the synthetic transcript.
		const summary = typeof entry.summary === "string" ? entry.summary.trim() : "";
		if (!summary) return null;
		return JSON.stringify({
			type: "user",
			message: {
				content: [{ type: "text", text: `[compaction summary]\n${summary}` }],
			},
		});
	}
	if (entry.type !== "message") return null;
	if (!isRecord(entry.message)) return null;

	const message = entry.message as { role: string; content: unknown; toolName?: string };
	if (message.role === "user") {
		return JSON.stringify({
			type: "user",
			message: {
				content: normalizeUserContent(message as UserMessageLike),
			},
		});
	}
	if (message.role === "assistant") {
		return JSON.stringify({
			type: "assistant",
			message: {
				content: normalizeAssistantContent(message as AssistantMessageLike),
			},
		});
	}
	if (message.role === "toolResult") {
		return serializeToolResultMessage(message as ToolResultMessageLike);
	}
	return null;
}

/** Serialize a session branch to transcript lines, dropping nulls. */
export function serializeBranchEntries(entries: SessionEntryLike[]): string[] {
	return entries
		.map((entry) => serializeEntry(entry))
		.filter((line): line is string => Boolean(line));
}
