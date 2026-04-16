import { describe, expect, it } from "bun:test";
import {
	buildAnthropicStatus,
	formatAnthropicStatusLines,
	maskSecret,
	parseAnthropicEnvSection,
} from "./status";

describe("maskSecret", () => {
	it("masks long tokens while preserving a short fingerprint", () => {
		expect(
			maskSecret("bcb9d50552cb4f8bb5238fb0d964a730.Uqq8O00r6ap215Rl"),
		).toBe("bcb9…15Rl");
	});

	it("returns generic masked text for short secrets", () => {
		expect(maskSecret("short")).toBe("set (masked)");
	});
});

describe("parseAnthropicEnvSection", () => {
	it("extracts supported keys from anthropic_env", () => {
		const parsed = parseAnthropicEnvSection(
			[
				"anthropic_env:",
				"  ANTHROPIC_AUTH_TOKEN: cfg-token",
				"  ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic",
				"  API_TIMEOUT_MS: 3000000",
			].join("\n"),
		);

		expect(parsed.ANTHROPIC_AUTH_TOKEN).toBe("cfg-token");
		expect(parsed.ANTHROPIC_BASE_URL).toBe("https://api.z.ai/api/anthropic");
		expect(parsed.API_TIMEOUT_MS).toBe("3000000");
	});

	it("ignores values outside anthropic_env", () => {
		const parsed = parseAnthropicEnvSection(
			[
				"defaults:",
				"  sonnet_model: claude-sonnet-4-6",
				"anthropic_env:",
				"  ANTHROPIC_DEFAULT_SONNET_MODEL: GLM-5.1",
			].join("\n"),
		);

		expect(parsed.ANTHROPIC_DEFAULT_SONNET_MODEL).toBe("GLM-5.1");
		expect((parsed as Record<string, string>).sonnet_model).toBeUndefined();
	});
});

describe("buildAnthropicStatus", () => {
	it("prefers process env over vault config", () => {
		const status = buildAnthropicStatus(
			{
				ANTHROPIC_BASE_URL: "https://env.example/anthropic",
			},
			[
				"anthropic_env:",
				"  ANTHROPIC_BASE_URL: https://vault.example/anthropic",
			].join("\n"),
		);

		const item = status.items.find((entry) => entry.key === "ANTHROPIC_BASE_URL");
		expect(item).toEqual({
			key: "ANTHROPIC_BASE_URL",
			source: "env",
			valuePreview: "https://env.example/anthropic",
			isSecret: false,
		});
	});

	it("uses vault config when env is absent", () => {
		const status = buildAnthropicStatus(
			{},
			[
				"anthropic_env:",
				"  ANTHROPIC_DEFAULT_HAIKU_MODEL: GLM-5-TURBO",
			].join("\n"),
		);

		const item = status.items.find(
			(entry) => entry.key === "ANTHROPIC_DEFAULT_HAIKU_MODEL",
		);
		expect(item?.source).toBe("vault config");
		expect(item?.valuePreview).toBe("GLM-5-TURBO");
	});

	it("marks missing values as unset", () => {
		const status = buildAnthropicStatus({}, undefined);
		const item = status.items.find((entry) => entry.key === "API_TIMEOUT_MS");
		expect(item?.source).toBe("unset");
		expect(item?.valuePreview).toBe("unset");
	});

	it("masks secret values in previews", () => {
		const status = buildAnthropicStatus(
			{},
			[
				"anthropic_env:",
				"  ANTHROPIC_AUTH_TOKEN: bcb9d50552cb4f8bb5238fb0d964a730.Uqq8O00r6ap215Rl",
			].join("\n"),
		);

		const item = status.items.find((entry) => entry.key === "ANTHROPIC_AUTH_TOKEN");
		expect(item?.source).toBe("vault config");
		expect(item?.valuePreview).toBe("bcb9…15Rl");
		expect(item?.isSecret).toBe(true);
	});

	it("reports a warning for malformed config while keeping env-derived items", () => {
		const status = buildAnthropicStatus(
			{ API_TIMEOUT_MS: "3000000" },
			"anthropic_env:\n  [broken",
		);

		expect(status.warning).toContain("vault config");
		const item = status.items.find((entry) => entry.key === "API_TIMEOUT_MS");
		expect(item?.source).toBe("env");
	});
});

describe("formatAnthropicStatusLines", () => {
	it("renders a readable status block", () => {
		const lines = formatAnthropicStatusLines({
			items: [
				{
					key: "ANTHROPIC_BASE_URL",
					source: "vault config",
					valuePreview: "https://api.z.ai/api/anthropic",
					isSecret: false,
				},
			],
		});

		expect(lines).toContain("Anthropic config:");
		expect(lines).toContain(
			"ANTHROPIC_BASE_URL: vault config (https://api.z.ai/api/anthropic)",
		);
	});
});
