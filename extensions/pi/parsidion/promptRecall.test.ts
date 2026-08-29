import { describe, expect, it } from "bun:test";
import { buildRecallPayload, recallResponseToContent, shouldRecall } from "./lib/promptRecall";

describe("shouldRecall", () => {
	it("skips empty and whitespace-only prompts", () => {
		expect(shouldRecall("")).toBe(false);
		expect(shouldRecall("  \n\t ")).toBe(false);
	});

	it("skips slash commands", () => {
		expect(shouldRecall("/parsidion")).toBe(false);
		expect(shouldRecall("  /skill foo")).toBe(false);
	});

	it("accepts normal prompts", () => {
		expect(shouldRecall("How did we fix the flaky login test?")).toBe(true);
	});
});

describe("buildRecallPayload", () => {
	it("builds the shared snake_case payload", () => {
		expect(buildRecallPayload("hello", "/tmp/work", "session-1")).toEqual({
			prompt: "hello",
			cwd: "/tmp/work",
			session_id: "session-1",
		});
	});

	it("omits an absent session id", () => {
		expect(buildRecallPayload("hello", "/tmp/work")).toEqual({ prompt: "hello", cwd: "/tmp/work" });
	});
});

describe("recallResponseToContent", () => {
	it("reads hookSpecificOutput.additionalContext from stdout", () => {
		const stdout = JSON.stringify({ hookSpecificOutput: { additionalContext: "vault facts" } });
		expect(recallResponseToContent(stdout)).toBe("vault facts");
	});

	it("accepts an already parsed response", () => {
		expect(recallResponseToContent({ hookSpecificOutput: { additionalContext: "vault facts" } })).toBe("vault facts");
	});

	it("recovers JSON after leading diagnostics", () => {
		expect(recallResponseToContent('diagnostic\n{"hookSpecificOutput":{"additionalContext":"facts"}}')).toBe("facts");
	});

	it("returns undefined for absent or garbage responses", () => {
		expect(recallResponseToContent("{}")).toBeUndefined();
		expect(recallResponseToContent("not json")).toBeUndefined();
		expect(recallResponseToContent(42)).toBeUndefined();
	});

	it("returns undefined for non-string or empty context", () => {
		expect(recallResponseToContent({ hookSpecificOutput: { additionalContext: 42 } })).toBeUndefined();
		expect(recallResponseToContent({ hookSpecificOutput: { additionalContext: "  " } })).toBeUndefined();
	});
});
