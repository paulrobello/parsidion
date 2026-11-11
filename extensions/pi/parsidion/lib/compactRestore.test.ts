import { describe, expect, it } from "bun:test";
import {
	buildCompactRestorePayload,
	COMPACT_RESTORE_SCRIPT,
	COMPACT_RESTORE_TIMEOUT_MS,
} from "./compactRestore";

describe("compactRestore", () => {
	it("invokes the SessionStart script, not the logging-only post_compact_hook.py", () => {
		expect(COMPACT_RESTORE_SCRIPT).toBe("session_start_hook.py");
	});

	it("builds the source=compact payload the hook expects", () => {
		expect(buildCompactRestorePayload("/tmp/work")).toEqual({ cwd: "/tmp/work", source: "compact" });
	});

	it("uses the SessionStart budget, not the old 6 s PostCompact timeout", () => {
		expect(COMPACT_RESTORE_TIMEOUT_MS).toBe(60_000);
	});
});
