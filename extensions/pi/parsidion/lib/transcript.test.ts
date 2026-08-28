import { describe, expect, it } from "bun:test";
import {
	PI_TO_CLAUDE_TOOL_MAP,
	serializeBranchEntries,
	serializeEntry,
	type SessionEntryLike,
} from "./transcript";

// Regression pin (kanban 01a0463a99467e619bd092bded7671cb): pi's SessionEntry
// union drives the synthetic session-stop transcript. When every entry drops,
// ephemeral omp sessions produce 0-byte transcripts and are never summarized.
//
// The union below mirrors @oh-my-pi/pi-coding-agent 18.0.7
// (dist/types/session/session-entries.d.ts, verified 2026-08-27 against the
// installed omp 18.0.8). When pi grows the union, extend the fixtures here:
// every NEW content-bearing entry type must serialize; control entries assert
// null so a accidental content-carrying rename fails loudly instead of
// silently yielding empty transcripts.
//
// Union members and expected outcomes:
//   message (user/assistant/toolResult) -> line
//   message (developer)                 -> null (system-injected, not conversation)
//   compaction                          -> line (summary replaces the pre-compaction branch)
//   custom_message                      -> line
//   thinking_level_change               -> null
//   model_change                        -> null
//   service_tier_change                 -> null
//   branch_summary                      -> null (branch-fork marker)
//   custom                              -> null (extension state, not LLM context)
//   label                               -> null
//   title_change                        -> null
//   ttsr_injection                      -> null
//   session_init                        -> null (subagent sessions; not on the main branch)
//   mode_change                         -> null
//   credential_pin                      -> null
//   reset_boundary                      -> null

function messageEntry(message: Record<string, unknown>): SessionEntryLike {
	return { type: "message", id: "e1", parentId: null, timestamp: "2026-08-27T00:00:00Z", message };
}

describe("serializeEntry — content-bearing entries", () => {
	it("serializes a user message with string content", () => {
		const line = serializeEntry(messageEntry({ role: "user", content: "hello vault" }));
		expect(line).not.toBeNull();
		expect(JSON.parse(line as string)).toEqual({
			type: "user",
			message: { content: "hello vault" },
		});
	});

	it("serializes a user message with block content", () => {
		const line = serializeEntry(
			messageEntry({ role: "user", content: [{ type: "text", text: "part one" }, { type: "text", text: "part two" }] }),
		);
		expect(line).not.toBeNull();
		const parsed = JSON.parse(line as string);
		expect(parsed.type).toBe("user");
		expect(parsed.message.content).toEqual([
			{ type: "text", text: "part one" },
			{ type: "text", text: "part two" },
		]);
	});

	it("serializes an assistant message with text and a mapped tool call", () => {
		const line = serializeEntry(
			messageEntry({
				role: "assistant",
				content: [
					{ type: "text", text: "listing files" },
					{ type: "toolCall", name: "bash", arguments: { command: "ls -la" } },
				],
			}),
		);
		expect(line).not.toBeNull();
		const parsed = JSON.parse(line as string);
		expect(parsed.type).toBe("assistant");
		expect(parsed.message.content).toEqual([
			{ type: "text", text: "listing files" },
			{ type: "tool_use", name: "Bash", input: { command: "ls -la", timeout: undefined } },
		]);
	});

	it("drops unknown tool calls but keeps assistant text", () => {
		const line = serializeEntry(
			messageEntry({
				role: "assistant",
				content: [{ type: "text", text: "hm" }, { type: "toolCall", name: "not_a_mapped_tool", arguments: {} }],
			}),
		);
		expect(line).not.toBeNull();
		const parsed = JSON.parse(line as string);
		expect(parsed.message.content).toEqual([{ type: "text", text: "hm" }]);
	});

	it("serializes a toolResult message with text content", () => {
		const line = serializeEntry(
			messageEntry({ role: "toolResult", toolName: "read", content: [{ type: "text", text: "file body" }] }),
		);
		expect(line).not.toBeNull();
		const parsed = JSON.parse(line as string);
		expect(parsed.type).toBe("user");
		expect(parsed.message.content[0].text).toBe("[tool:read]\nfile body");
	});

	it("drops a toolResult with empty text", () => {
		const line = serializeEntry(
			messageEntry({ role: "toolResult", toolName: "read", content: [{ type: "text", text: "   " }] }),
		);
		expect(line).toBeNull();
	});

	it("serializes a compaction entry's summary (replaces the pre-compaction branch)", () => {
		const line = serializeEntry({
			type: "compaction",
			summary: "The user refactored the installer to honor XDG_CONFIG_HOME.",
			firstKeptEntryId: "e9",
			tokensBefore: 40_000,
		});
		expect(line).not.toBeNull();
		const parsed = JSON.parse(line as string);
		expect(parsed.type).toBe("user");
		expect(parsed.message.content[0].text).toContain("[compaction summary]");
		expect(parsed.message.content[0].text).toContain("XDG_CONFIG_HOME");
	});

	it("drops a compaction entry with no summary", () => {
		expect(serializeEntry({ type: "compaction", summary: "  ", firstKeptEntryId: "e9", tokensBefore: 1 })).toBeNull();
	});

	it("serializes a custom_message entry", () => {
		const line = serializeEntry({ type: "custom_message", customType: "x", content: [{ type: "text", text: "injected" }], display: true });
		expect(line).not.toBeNull();
		expect(JSON.parse(line as string).message.content[0].text).toBe("injected");
	});
});

describe("serializeEntry — control entries return null", () => {
	const controlEntries: Array<[string, SessionEntryLike]> = [
		["developer message", messageEntry({ role: "developer", content: "system-ish" })],
		["thinking_level_change", { type: "thinking_level_change", thinkingLevel: "high" }],
		["model_change", { type: "model_change", model: "anthropic/claude-fable-5" }],
		["service_tier_change", { type: "service_tier_change", serviceTier: null }],
		["branch_summary", { type: "branch_summary", fromId: "e1", summary: "fork point" }],
		["custom", { type: "custom", customType: "parsidion:subagent-processed", data: {} }],
		["label", { type: "label", targetId: "e1", label: "bookmark" }],
		["title_change", { type: "title_change", title: "new title", source: "user" }],
		["ttsr_injection", { type: "ttsr_injection", injectedRules: ["r1"] }],
		["session_init", { type: "session_init", systemPrompt: "sp", task: "t", tools: [] }],
		["mode_change", { type: "mode_change", mode: "plan" }],
		["credential_pin", { type: "credential_pin", provider: "anthropic", hash: "abc" }],
		["reset_boundary", { type: "reset_boundary" }],
	];

	for (const [name, entry] of controlEntries) {
		it(`${name} -> null`, () => {
			expect(serializeEntry(entry)).toBeNull();
		});
	}
});

describe("serializeBranchEntries", () => {
	it("keeps serialized lines and drops control entries", () => {
		const lines = serializeBranchEntries([
			{ type: "model_change", model: "anthropic/claude-fable-5" },
			messageEntry({ role: "user", content: "do the thing" }),
			{ type: "reset_boundary" },
			messageEntry({ role: "assistant", content: [{ type: "text", text: "done" }] }),
		]);
		expect(lines).toHaveLength(2);
		expect(JSON.parse(lines[0]).message.content).toBe("do the thing");
		expect(JSON.parse(lines[1]).message.content[0].text).toBe("done");
	});

	it("returns [] for an all-control branch (caller must skip, never write 0 bytes)", () => {
		const lines = serializeBranchEntries([
			{ type: "model_change", model: "m" },
			{ type: "credential_pin", provider: "p", hash: "h" },
		]);
		expect(lines).toEqual([]);
	});
});

describe("PI_TO_CLAUDE_TOOL_MAP", () => {
	it("maps every pi tool name to a Claude tool", () => {
		for (const [piName, mapping] of Object.entries(PI_TO_CLAUDE_TOOL_MAP)) {
			expect(mapping.name.length).toBeGreaterThan(0);
			expect(typeof mapping.mapArgs).toBe("function");
			expect(piName.length).toBeGreaterThan(0);
		}
	});
});
