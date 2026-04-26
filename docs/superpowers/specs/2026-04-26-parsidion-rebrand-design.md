# Parsidion Rebrand and Provider Upgrade Design

Date: 2026-04-26

## Summary

Rename the project from `parsidion-cc` to `parsidion` and reposition it from a Claude Code-specific toolkit into an agent-agnostic memory and knowledge-vault layer for coding assistants. The rename is a hard cut: new package names, source paths, install paths, docs, and runtime hook commands use `parsidion`. The installer automatically detects and cleans legacy managed `parsidion-cc` assets.

Functionally, Parsidion will separate core vault behavior from runtime adapters and LLM providers. Claude Code remains supported as an adapter. Codex support is added as a runtime adapter using Codex hooks and transcripts. OpenAI support is added as an LLM provider using OpenAI Platform credentials.

## Goals

- Rename product, package, source paths, docs, and installed assets from `parsidion-cc` to `parsidion`.
- Automatically detect and clean old managed `parsidion-cc` hooks and skill assets during install.
- Preserve existing Claude Code behavior under the new `parsidion` identity.
- Introduce a clean design distinction between:
  - Parsidion core
  - runtime adapters
  - LLM providers
- Add an OpenAI provider path for Parsidion-owned LLM calls.
- Add a Codex runtime adapter path for Codex CLI sessions and hooks.
- Document the difference between Codex CLI subscription auth and OpenAI API billing.

## Non-goals

- Parsidion will not become a full replacement coding agent.
- The initial Codex adapter will not drive Codex as an agent through the Codex SDK.
- The project will not preserve `parsidion-cc` as a long-term compatibility install target.
- Parsidion will not copy, manage, or depend on `~/.codex/auth.json`.
- Unit tests will not make live OpenAI or Anthropic network calls.

## Product Identity

The product name is **Parsidion**.

Parsidion is an agent-agnostic memory and knowledge-vault layer for coding assistants. It gives coding agents persistent markdown memory, semantic search, session summarization, daily notes, research capture, vault maintenance, visual browsing, and MCP access.

The former `parsidion-cc` name is legacy only. New installs, package metadata, docs, logs, and hook commands use `parsidion`.

## Architecture Model

```text
Parsidion core
  - vault schema
  - note search/index
  - embeddings
  - summarization queue
  - daily notes
  - doctor/merge/review/export
  - visualizer/MCP

Runtime adapters
  - Claude Code adapter: ~/.claude hooks, skills, agents
  - Codex adapter: ~/.codex hooks, Codex transcript parser
  - pi adapter: ~/.pi integration
  - future adapters: Gemini CLI, Copilot CLI, etc.

LLM providers
  - Claude provider: claude-agent-sdk / claude -p / Anthropic-compatible transport
  - OpenAI provider: OPENAI_API_KEY / OpenAI Platform billing
  - future providers: OpenRouter, local Ollama, etc.
```

Core vault behavior must not depend on a specific coding-agent runtime. Runtime-specific details belong in adapter code. Model-provider details belong in provider code.

## Hard Rename

The rename is runtime-sensitive and must not be implemented as a blind text replacement.

Required updates include:

- `pyproject.toml`
  - project name: `parsidion`
  - setuptools package-dir: `skills/parsidion/scripts`
  - pytest, coverage, pyright, and ty paths updated.
- Source directory
  - `skills/parsidion-cc/` renamed to `skills/parsidion/`.
- Installer
  - `SKILL_SRC` points to `skills/parsidion`.
  - hook commands point to `~/.claude/skills/parsidion/scripts/...`.
  - CLI help says Parsidion, not Parsidion CC.
- Runtime/log names
  - progress and log filenames use `parsidion-*`, not `parsidion-cc-*`.
- Docs
  - README, CLAUDE.md, AGENTS.md, SECURITY.md, docs pages, templates, and extension docs use the new name and positioning.
- Tests
  - path expectations updated.
  - legacy cleanup behavior covered by tests.

Intentional remaining `parsidion-cc` strings are allowed only in migration code, cleanup tests, changelog/history, and docs that explicitly describe legacy cleanup.

## Legacy Cleanup

Install automatically detects and removes old managed `parsidion-cc` assets. Cleanup is default behavior, not opt-in.

During install, before registering new hooks, the installer will:

1. Remove old managed hook entries whose command references `skills/parsidion-cc/scripts/...`.
2. Remove old `~/.claude/skills/parsidion-cc/` directory or symlink when present.
3. Remove or rewrite old managed guidance references that point to `parsidion-cc` paths.
4. Preserve user vault contents under `~/ClaudeVault/`.
5. Register new hooks that point to `~/.claude/skills/parsidion/scripts/...`.

`--dry-run` reports all cleanup actions without writing. `--uninstall` removes current `parsidion` assets and also cleans legacy managed `parsidion-cc` assets.

Cleanup should only remove assets Parsidion manages. It must not delete user notes, vault contents, unrelated Claude settings, or unrelated hook entries.

## Provider Abstraction

Parsidion should separate LLM-using tasks from the provider that runs them.

A likely module layout is:

```text
skills/parsidion/scripts/llm/
  __init__.py
  types.py
  router.py
  claude_provider.py
  openai_provider.py
```

The provider interface should cover simple text completion first:

```python
complete(prompt, model, *, timeout, system=None) -> str
```

An async equivalent may be added for summarizer concurrency.

Provider modules are responsible for:

- provider-specific environment and auth resolution
- provider-specific SDK/subprocess calls
- error normalization
- lazy imports of provider-specific dependencies

Provider modules are not responsible for vault writes, note formatting, transcript parsing, or business logic.

### Claude Provider

The Claude provider preserves current default behavior:

- `summarize_sessions.py` can continue using `claude-agent-sdk` behind the provider boundary.
- hook, doctor, merge, and related call sites can continue using `claude -p` behind the provider boundary where that is the safest migration path.
- `claude-agent-sdk` imports move inside Claude-specific code so non-Claude provider paths do not require the SDK.
- Existing Anthropic-compatible config remains supported during migration.

### OpenAI Provider

The OpenAI provider uses `OPENAI_API_KEY` and OpenAI Platform billing for Parsidion-owned LLM calls.

It must not imply that a ChatGPT or Codex subscription is a general-purpose OpenAI API credential.

Suggested config direction:

```yaml
llm:
  default_provider: claude

providers:
  claude:
    sonnet_model: claude-sonnet-4-6
    haiku_model: claude-haiku-4-5-20251001
  openai:
    model: gpt-5.1
    mini_model: gpt-5.1-mini
    base_url: null
```

Existing `anthropic_env` can remain for Anthropic-compatible Claude transport while provider-aware config is introduced.

### Call Sites to Migrate

Provider abstraction should be introduced incrementally at these call sites:

- `summarize_sessions.py`
- `session_start_hook.py`
- `session_stop_hook.py`
- `vault_doctor.py`
- `vault_merge.py`
- `vault_links.py`
- evaluation scripts where practical

## Codex Runtime Adapter

Codex support is a runtime adapter, separate from OpenAI provider support.

Codex CLI/SDK can use the user's logged-in Codex CLI auth, including ChatGPT subscription access. Parsidion's own OpenAI provider calls use `OPENAI_API_KEY` and OpenAI Platform billing.

### Adapter Responsibilities

The Codex adapter will:

- detect Codex CLI availability
- resolve `CODEX_HOME`, defaulting to `~/.codex`
- detect or document `[features] codex_hooks = true`
- install or update Codex hook config where appropriate
- map Codex hook payloads to Parsidion hook scripts
- allow Codex transcript/session paths under `~/.codex`
- parse Codex session JSONL after fixture validation
- queue Codex sessions into `pending_summaries.jsonl`

### Initial Hook Support

The first supported Codex hooks are:

1. `SessionStart`
   - return vault context at session start.
2. `Stop`
   - queue the session transcript for summarization.

`UserPromptSubmit` is deferred until the basic adapter is proven. It may later inject targeted vault context per prompt.

### Codex SDK Position

`@openai/codex-sdk` exists, but it wraps the local `codex` CLI and communicates over JSONL. Parsidion should not require this SDK for basic Codex support.

Use Codex native hooks first. Consider the SDK later only if Parsidion needs to drive Codex agents directly.

### Auth Documentation

Docs must clearly state:

- Codex runtime integration can use a logged-in Codex CLI.
- Codex CLI can use ChatGPT/Codex subscription auth.
- Parsidion's OpenAI provider requires `OPENAI_API_KEY` and Platform billing.
- Parsidion does not copy or manage `~/.codex/auth.json`.
- CI users should follow Codex's recommended API-key workflow.

## Implementation Phases

### Phase 1: Hard Rename

Rename repo-visible product/package/source paths and update runtime hook paths.

Verification:

- `rg "parsidion-cc"` only returns intentional legacy cleanup/history references.
- `uv run install.py --dry-run --yes` shows new `parsidion` install targets.
- tests pass.
- docs no longer describe Parsidion as Claude Code-only.

### Phase 2: Legacy Cleanup

Add default installer cleanup for old managed `parsidion-cc` hooks and assets.

Verification:

- fixture `settings.json` with old hook commands is cleaned.
- old skill directory/symlink removal is dry-run safe.
- new hooks are registered after cleanup.
- `--uninstall` handles both current and legacy managed assets.

### Phase 3: Provider Abstraction

Move current Claude behavior behind a provider interface and add an OpenAI provider.

Verification:

- default config preserves existing Claude behavior.
- provider router tests pass.
- OpenAI provider tests are mocked.
- `claude-agent-sdk` is lazily imported only when the Claude SDK provider path is used.

### Phase 4: Codex Runtime Adapter Prototype

Add low-risk Codex support from real fixtures.

Verification:

- `~/.codex` transcript roots are accepted.
- Codex hook payload fixtures parse.
- Codex session JSONL fixtures parse.
- Codex `SessionStart` can return context.
- Codex `Stop` can queue summaries.

### Phase 5: Docs and Release Polish

Update documentation and release notes for the new identity and integration model.

Verification:

- README quick start is accurate.
- security docs mention both `~/.claude` and `~/.codex` surfaces.
- changelog includes migration warning.
- existing users know install removes old managed `parsidion-cc` assets.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Rename misses runtime-critical paths | Use targeted grep inventory and tests for installer commands, package paths, and hook registration. |
| Legacy cleanup deletes user content | Restrict cleanup to known managed paths and hooks; preserve vault contents. |
| Provider refactor changes Claude behavior | Add Claude-preservation tests and keep default provider as Claude initially. |
| OpenAI auth expectations are misunderstood | Clearly document OpenAI API key requirement for provider calls. |
| Codex transcript format differs from assumptions | Require real fixture tests before production summarization. |
| Codex SDK stability issues | Use native hooks first; avoid SDK dependency for basic integration. |

## Implementation Planning Defaults

These decisions keep the first implementation plan bounded:

- Provider abstraction and OpenAI support should be separate commits within the provider phase: first preserve Claude behavior behind the interface, then add OpenAI.
- Codex hook installation should start with global user config under `~/.codex`; repo-local Codex hook config can be added later.
- The `parsidion-vault` pi extension name remains unchanged for the rebrand unless a later design specifically broadens extension naming.
- GitHub repository and Pages URL changes are release tasks. Code and docs should prefer the new `parsidion` name, but external URL redirects may temporarily mention `parsidion-cc` until the repository is renamed.
