import { existsSync, readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const ANTHROPIC_STATUS_KEYS = [
	"ANTHROPIC_API_KEY",
	"ANTHROPIC_AUTH_TOKEN",
	"ANTHROPIC_BASE_URL",
	"ANTHROPIC_CUSTOM_HEADERS",
	"API_TIMEOUT_MS",
	"ANTHROPIC_DEFAULT_HAIKU_MODEL",
	"ANTHROPIC_DEFAULT_SONNET_MODEL",
	"ANTHROPIC_DEFAULT_OPUS_MODEL",
] as const;

const SECRET_KEYS = new Set<string>([
	"ANTHROPIC_API_KEY",
	"ANTHROPIC_AUTH_TOKEN",
]);

export type AnthropicStatusKey = (typeof ANTHROPIC_STATUS_KEYS)[number];
export type AnthropicStatusSource = "env" | "vault config" | "unset";

export type AnthropicStatusItem = {
	key: AnthropicStatusKey;
	source: AnthropicStatusSource;
	valuePreview: string;
	isSecret: boolean;
};

export type AnthropicStatusResult = {
	items: AnthropicStatusItem[];
	warning?: string;
};

export type VaultConfigReadResult = {
	text?: string;
	notice?: string;
	path: string;
};

function isAnthropicStatusKey(value: string): value is AnthropicStatusKey {
	return (ANTHROPIC_STATUS_KEYS as readonly string[]).includes(value);
}

function normalizeScalar(rawValue: string): string | undefined {
	const withoutComment = rawValue.split(/\s+#/, 1)[0]?.trim() ?? "";
	const cleaned = withoutComment.replace(/^['"]|['"]$/g, "").trim();
	if (!cleaned || cleaned === "null") return undefined;
	return cleaned;
}

export function maskSecret(value: string): string {
	const trimmed = value.trim();
	if (trimmed.length < 10) return "set (masked)";
	return `${trimmed.slice(0, 4)}…${trimmed.slice(-4)}`;
}

export function parseAnthropicEnvSection(
	text: string,
): Partial<Record<AnthropicStatusKey, string>> {
	const result: Partial<Record<AnthropicStatusKey, string>> = {};
	const lines = text.split(/\r?\n/);
	let inSection = false;

	for (const line of lines) {
		const trimmed = line.trim();
		if (!trimmed || trimmed.startsWith("#")) continue;

		const indent = line.length - line.trimStart().length;
		if (indent === 0) {
			inSection = trimmed === "anthropic_env:";
			continue;
		}
		if (!inSection) continue;

		if (indent < 2) {
			inSection = false;
			continue;
		}

		const colon = trimmed.indexOf(":");
		if (colon <= 0) {
			throw new Error(`invalid anthropic_env line: ${trimmed}`);
		}

		const key = trimmed.slice(0, colon).trim();
		if (!isAnthropicStatusKey(key)) continue;
		const value = normalizeScalar(trimmed.slice(colon + 1));
		if (value !== undefined) {
			result[key] = value;
		}
	}

	return result;
}

export function buildAnthropicStatus(
	env: Record<string, string | undefined>,
	vaultConfigText?: string,
): AnthropicStatusResult {
	let configValues: Partial<Record<AnthropicStatusKey, string>> = {};
	let warning: string | undefined;

	if (vaultConfigText) {
		try {
			configValues = parseAnthropicEnvSection(vaultConfigText);
		} catch (error) {
			warning = `vault config unreadable: ${
				error instanceof Error ? error.message : String(error)
			}`;
		}
	}

	const items = ANTHROPIC_STATUS_KEYS.map((key) => {
		const envValue = env[key]?.trim();
		const configValue = configValues[key]?.trim();
		const isSecret = SECRET_KEYS.has(key);

		if (envValue) {
			return {
				key,
				source: "env" as const,
				valuePreview: isSecret ? maskSecret(envValue) : envValue,
				isSecret,
			};
		}

		if (configValue) {
			return {
				key,
				source: "vault config" as const,
				valuePreview: isSecret ? maskSecret(configValue) : configValue,
				isSecret,
			};
		}

		return {
			key,
			source: "unset" as const,
			valuePreview: "unset",
			isSecret,
		};
	});

	return warning ? { items, warning } : { items };
}

export function resolveVaultConfigPath(
	env: Record<string, string | undefined>,
): string {
	const vaultRoot = env.CLAUDE_VAULT?.trim() || path.join(os.homedir(), "ClaudeVault");
	return path.join(vaultRoot, "config.yaml");
}

export function readVaultConfigText(
	env: Record<string, string | undefined>,
): VaultConfigReadResult {
	const configPath = resolveVaultConfigPath(env);
	if (!existsSync(configPath)) {
		return {
			path: configPath,
			notice: `vault config: not found (${configPath})`,
		};
	}
	try {
		return {
			path: configPath,
			text: readFileSync(configPath, "utf8"),
		};
	} catch (error) {
		return {
			path: configPath,
			notice: `vault config unreadable (${configPath}): ${
				error instanceof Error ? error.message : String(error)
			}`,
		};
	}
}

export function formatAnthropicStatusLines(status: AnthropicStatusResult & {
	notice?: string;
}): string[] {
	const lines = ["Anthropic config:"];
	if (status.notice) lines.push(`note: ${status.notice}`);
	if (status.warning) lines.push(`warning: ${status.warning}`);
	for (const item of status.items) {
		lines.push(`${item.key}: ${item.source} (${item.valuePreview})`);
	}
	lines.push("runtime authority: Python hook scripts");
	return lines;
}
