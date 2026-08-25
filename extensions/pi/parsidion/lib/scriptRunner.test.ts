import { describe, expect, it, afterEach, beforeEach } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
	buildHookEnv,
	candidateScriptDirs,
	resolveScriptDir,
	SCRIPT_REQUIRED_FILES,
} from "./scriptRunner";

const ENV_KEYS = ["PARSIDION_SCRIPTS_DIR", "PARSIDION_DIR"] as const;

describe("candidateScriptDirs (SEC-003)", () => {
	let savedEnv: Record<string, string | undefined>;

	beforeEach(() => {
		savedEnv = {};
		for (const key of ENV_KEYS) {
			savedEnv[key] = process.env[key];
			delete process.env[key];
		}
	});

	afterEach(() => {
		for (const key of ENV_KEYS) {
			const value = savedEnv[key];
			if (value === undefined) delete process.env[key];
			else process.env[key] = value;
		}
	});

	it("never returns a cwd-relative sibling directory", () => {
		// Pre-fix, a clone of any repo named `parsidion` beside the workspace
		// was executed ahead of the installed scripts (CWE-427).
		const dirs = candidateScriptDirs("/tmp/some-workspace");
		expect(dirs.every((dir) => path.isAbsolute(dir))).toBe(true);
		expect(dirs.some((dir) => dir.includes("some-workspace"))).toBe(false);
		expect(dirs.some((dir) => dir.endsWith(".."))).toBe(false);
	});

	it("orders PARSIDION_SCRIPTS_DIR first, then PARSIDION_DIR, then the installed path", () => {
		process.env.PARSIDION_SCRIPTS_DIR = "/opt/scripts";
		process.env.PARSIDION_DIR = "/opt/repo";
		const dirs = candidateScriptDirs("/tmp/ws");
		expect(dirs[0]).toBe("/opt/scripts");
		expect(dirs[1]).toBe(path.join("/opt/repo", "skills", "parsidion", "scripts"));
		expect(dirs[2]).toBe(path.join(os.homedir(), ".claude", "skills", "parsidion", "scripts"));
		expect(dirs).toHaveLength(3);
	});

	it("skips unset candidates", () => {
		const dirs = candidateScriptDirs("/tmp/ws");
		expect(dirs).toHaveLength(1);
		expect(dirs[0]).toBe(path.join(os.homedir(), ".claude", "skills", "parsidion", "scripts"));
	});
});

describe("resolveScriptDir ignores a planted sibling repo (SEC-003)", () => {
	let savedScriptDir: string | undefined;
	let savedHome: string | undefined;
	let tmp: string;

	beforeEach(() => {
		savedScriptDir = process.env.PARSIDION_SCRIPTS_DIR;
		savedHome = process.env.HOME;
		delete process.env.PARSIDION_SCRIPTS_DIR;
		tmp = mkdtempSync(path.join(os.tmpdir(), "sec003-"));
		// Point HOME at an empty tree so the installed-path fallback (the last
		// candidate, derived from os.homedir()) cannot resolve on a machine
		// that really has parsidion installed.
		process.env.HOME = path.join(tmp, "fake-home");
	});

	afterEach(() => {
		if (savedScriptDir === undefined) delete process.env.PARSIDION_SCRIPTS_DIR;
		else process.env.PARSIDION_SCRIPTS_DIR = savedScriptDir;
		if (savedHome === undefined) delete process.env.HOME;
		else process.env.HOME = savedHome;
		rmSync(tmp, { recursive: true, force: true });
	});

	it("does not resolve scripts from ../parsidion relative to the workspace cwd", () => {
		// Plant a complete hook set in <tmp>/parsidion/skills/parsidion/scripts
		// next to the workspace at <tmp>/workspace — exactly the pre-fix attack.
		const planted = path.join(tmp, "parsidion", "skills", "parsidion", "scripts");
		mkdirSync(planted, { recursive: true });
		for (const file of SCRIPT_REQUIRED_FILES) {
			writeFileSync(path.join(planted, file), "#!/usr/bin/env python3\nprint('{}')\n");
		}
		const workspace = path.join(tmp, "workspace");
		mkdirSync(workspace, { recursive: true });

		// The planted sibling must never be chosen. (On a machine where
		// parsidion really is installed, resolveScriptDir legitimately returns
		// the installed path — bun caches os.homedir(), so the HOME override
		// in beforeEach cannot mask it there. The planted dir not being the
		// answer is the invariant under test.)
		const resolved = resolveScriptDir(workspace);
		expect(resolved).not.toBe(planted);
		// And the workspace's candidates never contained it in the first place.
		const candidates = candidateScriptDirs(workspace);
		expect(candidates.some((dir) => dir.startsWith(tmp))).toBe(false);
	});

	it("resolves PARSIDION_SCRIPTS_DIR when it contains the full hook set", () => {
		const scripts = path.join(tmp, "scripts");
		mkdirSync(scripts, { recursive: true });
		for (const file of SCRIPT_REQUIRED_FILES) {
			writeFileSync(path.join(scripts, file), "#!/usr/bin/env python3\nprint('{}')\n");
		}
		process.env.PARSIDION_SCRIPTS_DIR = scripts;
		expect(resolveScriptDir(path.join(tmp, "workspace"))).toBe(scripts);
	});
});

describe("buildHookEnv (SEC-003)", () => {
	it("drops CLAUDECODE and secrets outside the allowlist", () => {
		const env = buildHookEnv({
			PATH: "/usr/bin",
			HOME: "/Users/x",
			CLAUDECODE: "1",
			AWS_SECRET_ACCESS_KEY: "topsecret",
			GITHUB_TOKEN: "gh-secret",
			NPM_TOKEN: "npm-secret",
		});
		expect(env.PATH).toBe("/usr/bin");
		expect(env.HOME).toBe("/Users/x");
		expect(env.CLAUDECODE).toBeUndefined();
		expect(env.AWS_SECRET_ACCESS_KEY).toBeUndefined();
		expect(env.GITHUB_TOKEN).toBeUndefined();
		expect(env.NPM_TOKEN).toBeUndefined();
	});

	it("forwards the Anthropic/proxy keys from _SAFE_ENV_KEYS", () => {
		const env = buildHookEnv({
			ANTHROPIC_API_KEY: "sk-ant",
			ANTHROPIC_BASE_URL: "https://proxy.example",
			API_TIMEOUT_MS: "30000",
			HTTPS_PROXY: "http://127.0.0.1:7890",
			PARSIGHT_MCP_URL: "http://127.0.0.1:4848/mcp",
		});
		expect(env.ANTHROPIC_API_KEY).toBe("sk-ant");
		expect(env.ANTHROPIC_BASE_URL).toBe("https://proxy.example");
		expect(env.API_TIMEOUT_MS).toBe("30000");
		expect(env.HTTPS_PROXY).toBe("http://127.0.0.1:7890");
		expect(env.PARSIGHT_MCP_URL).toBe("http://127.0.0.1:4848/mcp");
	});

	it("forwards LC_*/XDG_*/PARSIDION_* prefixed vars and CLAUDE_VAULT", () => {
		const env = buildHookEnv({
			LC_CTYPE: "UTF-8",
			XDG_CONFIG_HOME: "/Users/x/.config",
			PARSIDION_DIR: "/opt/parsidion",
			CLAUDE_VAULT: "/Users/x/ParsidionVault",
			CLAUDE_CODE_SOMETHING: "dropped",
		});
		expect(env.LC_CTYPE).toBe("UTF-8");
		expect(env.XDG_CONFIG_HOME).toBe("/Users/x/.config");
		expect(env.PARSIDION_DIR).toBe("/opt/parsidion");
		expect(env.CLAUDE_VAULT).toBe("/Users/x/ParsidionVault");
		expect(env.CLAUDE_CODE_SOMETHING).toBeUndefined();
	});
});
