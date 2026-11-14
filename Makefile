.PHONY: build test test-graph test-search lint fmt fmt-check typecheck checkall checkall-ci checkall-mcp wheel-install pi-extension-check clean install graph graph-with-daily visualizer stop-visualizer build-visualizer visualizer-setup visualizer-check parity-fixtures parity-fixtures-check docs-api bench-hooks grind-start grind-start-anthropic grind-start-zai grind-start-grok grind-start-codex grind-start-omp grind-stop grind-clean-logs

# Format code with ruff
fmt:
	uv run ruff format .

# Verify formatting without rewriting files (matches CI's format-check step)
fmt-check:
	uv run ruff format --check .

# Lint code with ruff
lint:
	uv run ruff check .

# Type check with pyright
typecheck:
	uv run pyright .

# Run tests
# NOTE: this leg shells out to real git (git_commit_vault, 10 s subprocess
# timeout) and is load-sensitive on a busy host: under high load average a
# git subprocess can exceed its timeout and fail one test in
# tests/test_atomic_write_fixes.py (selector.select timeout, code untouched).
# That failure class needs a low-load host or an isolated rerun of the
# failing test; everything else in the leg is load-tolerant.
test:
	uv run pytest tests/

# Run build_graph.py's parsight body-link enrichment tests (numpy-gated,
# skipped under `test`/`checkall`'s numpy-free default suite)
test-graph:
	uv run --with numpy python -c "import numpy" && uv run --with numpy pytest tests/test_build_graph_parsight.py

# Run the sqlite_vec-gated search suites (decay contract + vec0 ANN parity
# and fallback; skipped under `test`/`checkall`'s dependency-free default
# suite — a 2286edb stub break sat undetected in them for that reason)
test-search:
	uv run --with sqlite-vec --with fastembed pytest tests/test_search_decay_ordering.py tests/test_vec0_ann_search.py

# ENH-005: regenerate the cross-language parity fixtures:
#   - tests/fixtures/graph.schema.json (derived from build_graph.py:GRAPH_JSON_SCHEMA)
#   - tests/fixtures/parity/vault-resolution.json (structurally validated)
# Run after editing GRAPH_JSON_SCHEMA or a vault-resolution vector.
parity-fixtures:
	uv run python scripts/gen_parity_fixtures.py

# ENH-005: CI gate -- regenerate to a temp output and diff against the
# committed fixtures. Fails if GRAPH_JSON_SCHEMA or the vectors drifted.
# No mutation: the generator compares in-process and reports the diff.
parity-fixtures-check:
	uv run python scripts/gen_parity_fixtures.py --check

# === ENH-017: Generated config reference ==========================================
# `make config-docs` regenerates the CLAUDE.md config table, the
# docs/ARCHITECTURE.md reference block, templates/config.yaml, and the
# standalone copies under docs/generated/ from core/vault_schema.py field
# metadata. `make config-docs-check` fails on drift (CI gate).
config-docs:
	uv run python scripts/gen_config_docs.py

config-docs-check:
	uv run python scripts/gen_config_docs.py --check

# === ENH-011: Generated API reference =============================================
# `make docs-api` regenerates docs/api/ from docstrings (pdoc, `docs` extra)
# and JSDoc (typedoc, visualizer devDeps). ARC-206: docs/api is no longer a
# committed snapshot - CI runs this target and uploads docs/api as a workflow
# artifact, and pages.yml deploys it to GitHub Pages under /api/. Run it
# locally to preview what CI will publish.
#
# build_graph.py (numpy), summarize_sessions.py (anyio), and build_embeddings.py
# (fastembed/sqlite_vec guard) are skipped because their top-level imports are
# not satisfiable from the lightweight `docs` extra; html-to-md.py is a PEP 723
# script and not importable as a module (hyphen).
# ARC-200 step 2: the retired re-export shims (parsidion.vault_common,
# vault_config, vault_path, vault_fs, vault_index, vault_hooks, vault_adaptive,
# vault_metrics, vault_constants, vault_health, vault_links, subproc_util,
# ai_backend, parsight_backend) left this list - each stub raises ImportError
# at import, so pdoc has nothing to document.
PDOC_MODULES := parsidion.core installer parsidion.vault_tui parsidion.vault_new parsidion.vault_embed_serve \
	parsidion.note_schema parsidion.agent_adapter parsidion.prompt_templates \
	session_start_hook session_stop_hook pre_compact_hook post_compact_hook \
	subagent_stop_hook codex_session_start_hook codex_stop_hook codex_subagent_stop_hook \
	antigravity_session_start_hook antigravity_session_end_hook parsidion.vault_search \
	parsidion.vault_review parsidion.vault_export parsidion.vault_merge \
	parsidion.vault_conflicts vault_doctor parsidion.vault_stats update_index \
	check_graph_coverage run_trigger_eval vault_resolve

# vault_resolve stays a bare module: the visualizer spawns it by file path, so
# its repo path (and thus its pdoc page location) is part of the contract.

docs-api:
	rm -rf $(CURDIR)/docs/api
	PYTHONPATH="$(CURDIR)/skills/parsidion/scripts:$(CURDIR)" \
		uv run --isolated --python 3.13 --extra docs python -P -m pdoc \
		-o $(CURDIR)/docs/api/python $(PDOC_MODULES)
	cd visualizer && LC_ALL=C ls lib/*.ts | grep -v '\.test\.ts$$' | \
		xargs bunx typedoc --entryPointStrategy resolve \
		--out $(CURDIR)/docs/api/visualizer --options typedoc.json

# Typecheck, lint, unit-test, and build the visualizer (bun)
# 'bun run build' catches RSC server/client boundary violations (ARC-041) that tsc --noEmit alone misses
visualizer-check:
	cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build

# Run all checks in sequence: format check, lint, typecheck, test
checkall: fmt-check lint typecheck test test-graph test-search visualizer-check checkall-mcp config-docs-check

# CI-parity gate (ci-gate-parity convention): checkall plus every CI check
# that checkall alone does not run — the parity-fixtures drift gate, the wheel
# clean-room import smoke, and the pi extension bun tests. ARC-206: the
# docs-api drift gate is gone — CI generates docs/api as an artifact and Pages
# deploys it; there is no committed snapshot left to drift.
# checkall stays the fast inner loop; the heavier parity checks live here.
checkall-ci: checkall parity-fixtures-check wheel-install pi-extension-check

# Mirror of CI's wheel-install job (ARC-003): build the wheel, install it
# plus anyio into a throwaway seeded venv, and import every shipped module
# (scripts entries, py-modules, scripts subpackages — derived from
# pyproject.toml so new modules are covered automatically). Build output and
# the venv both live under one mktemp dir, so concurrent runs cannot collide
# and nothing lands in the checkout's dist/.
wheel-install:
	@tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT HUP INT TERM; \
	uv build --out-dir "$$tmp/dist" && \
	uv venv --python 3.13 --seed "$$tmp/venv" && \
	"$$tmp/venv/bin/pip" install --quiet --force-reinstall "$$tmp"/dist/*.whl anyio && \
	"$$tmp/venv/bin/python" - < scripts/check_wheel_imports.py

pi-extension-check:
	cd extensions/pi/parsidion && bun test
	# bun test only exercises lib/ modules; transpile the entry file too so a
	# syntax error in parsidion.ts fails the gate (its deps resolve only
	# inside pi/omp runtime loaders, hence --external).
	cd extensions/pi/parsidion && bun build ./parsidion.ts --target=bun --external '*' --outfile /dev/null

# Run parsidion-mcp checks (format, lint, typecheck, test)
checkall-mcp:
	$(MAKE) -C parsidion-mcp checkall

# ENH-023: SessionStart latency bench + budget gate (on-demand, NOT in
# checkall/CI — absolute budgets are machine-dependent). Run before releases
# and after touching session_start_hook.py / session_start/ / vault_index.py.
# Override per run: make bench-hooks BENCH_ARGS="--sizes 500 --reps 3"
bench-hooks:
	uv run --no-project tools/bench/bench_session_start.py $(BENCH_ARGS)

# Build (no-op for this project — it is managed configuration, not a compiled artifact)
build:
	@echo "parsidion is a configuration toolkit — no build step required."

# Clean generated artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true

# Install skill to ~/.claude (shortcut for uv run install.py --force --yes)
install:
	uv run install.py --force --yes

## Vault Visualizer
graph:
	uv run skills/parsidion/scripts/build_graph.py

graph-with-daily:
	uv run skills/parsidion/scripts/build_graph.py --include-daily

visualizer:
	cd visualizer && bun dev

stop-visualizer:
	@lsof -ti:3999 | xargs kill -9 2>/dev/null && echo "Visualizer stopped" || echo "Nothing running on port 3999"

build-visualizer:
	cd visualizer && bun run build

visualizer-setup:
	cd visualizer && bun install

# ==== Grind loop (grind.sh, lives in ~/Repos/par-grind) =====================
# Pasted from par-grind's examples/Makefile.grind (paste-not-include keeps
# help output clean; the fragment is the canonical copy). The loop runner is
# project-agnostic; this repo's knobs (prompt, clean command, post-iter
# check) would live in .grind.local.json, and every GRIND_* env var overrides
# a config key. See the par-grind README's config table.
#
# Invoked via `bash` on purpose: `ps` then shows `bash …/grind.sh <repo>`,
# the command shape grind's overseer recipes and probes match on. A
# shebang-direct invocation puts the script at a different ps field and reads
# as grind GONE to them.
GRIND_SH := $(HOME)/Repos/par-grind/grind.sh

grind-start: ## Run the grind loop against this repo (foreground; backend = GRIND_BACKEND or .grind.local.json)
	bash $(GRIND_SH) "$(CURDIR)"

grind-start-anthropic: ## Run the grind loop against the Anthropic backend (foreground)
	GRIND_BACKEND=anthropic bash $(GRIND_SH) "$(CURDIR)"

grind-start-zai: ## Run the grind loop against the z.ai backend (foreground; needs ZAI_API_KEY)
	GRIND_BACKEND=zai bash $(GRIND_SH) "$(CURDIR)"

grind-start-grok: ## Run the grind loop against the Grok backend (foreground; needs grok CLI login)
	GRIND_BACKEND=grok bash $(GRIND_SH) "$(CURDIR)"

grind-start-codex: ## Run the grind loop against the Codex backend (foreground; needs codex CLI login)
	GRIND_BACKEND=codex bash $(GRIND_SH) "$(CURDIR)"

# The omp arm is the only one with NO default model: OMP v18 applies
# --provider during explicit model resolution, so grind.sh derives omp's
# --provider from the exact model id (glm-5.3/glm-5.3-flash -> zai,
# gpt-5.6-terra/gpt-5.6-luna -> openai-codex, grok-4.6 -> xai-oauth).
# Refuse to launch without one rather than guess a provider.
grind-start-omp: ## Run the grind loop against the OMP backend (foreground; GRIND_MODEL required — provider derived from the exact model id)
	@test -n "$(GRIND_MODEL)" || { echo "grind-start-omp: GRIND_MODEL is required, e.g. GRIND_MODEL=glm-5.3 — the omp arm derives omp's --provider from the exact model id (glm-5.3/glm-5.3-flash->zai, gpt-5.6-terra/gpt-5.6-luna->openai-codex, grok-4.6->xai-oauth)" >&2; exit 2; }
	GRIND_BACKEND=omp GRIND_MODEL="$(GRIND_MODEL)" bash $(GRIND_SH) "$(CURDIR)"

grind-stop: ## Ask a running grind loop to stop after the current iteration
	@touch .exit-grind
	@echo "wrote .exit-grind — the loop stops after the current iteration finishes"

# .exit-grind is checked only at the top of each iteration: a stop takes
# effect once the in-flight session ends, and grind removes the file itself
# on exit — a grind-stop with nothing running just clears a stale sentinel.
# grind-start runs in the FOREGROUND (it owns the terminal); grind-stop runs
# from a second shell.

# 2*/ matches only the date-named run dirs — the latest symlink, the run.jsonl
# ledger, and stdout logs live at the root and never match. The newest 2 by
# mtime are kept (the active run is always newest, and the latest symlink's
# own target is excluded from removal as a belt-and-braces guard).
grind-clean-logs: ## Delete all but the newest 2 grind run dirs (latest symlink, run.jsonl kept)
	@latest=$$(readlink .grind-logs/latest 2>/dev/null | xargs basename 2>/dev/null); \
	old=$$(ls -dt .grind-logs/2*/ 2>/dev/null | tail -n +3 | sed 's:/$$::'); \
	[ -n "$$old" ] || { echo "grind-clean-logs: nothing to remove (<= 2 run dirs)"; exit 0; }; \
	if [ -n "$$latest" ]; then old=$$(echo "$$old" | grep -v "/$$latest$$"); fi; \
	[ -n "$$old" ] || { echo "grind-clean-logs: nothing to remove"; exit 0; }; \
	echo "$$old" | sed 's/^/  removing /'; \
	echo "$$old" | xargs rm -rf; \
	echo "grind-clean-logs: $$(ls -d .grind-logs/2*/ 2>/dev/null | wc -l | tr -d ' ') run dir(s) remain"
