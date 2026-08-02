.PHONY: build test test-graph lint fmt fmt-check typecheck checkall checkall-mcp clean install graph graph-with-daily visualizer stop-visualizer build-visualizer visualizer-setup visualizer-check parity-fixtures parity-fixtures-check docs-api docs-api-check docs-api-gen

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
test:
	uv run pytest tests/

# Run build_graph.py's par-mem body-link enrichment tests (numpy-gated,
# skipped under `test`/`checkall`'s numpy-free default suite)
test-graph:
	uv run --with numpy python -c "import numpy" && uv run --with numpy pytest tests/test_build_graph_parmem.py

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

# === ENH-011: Generated API reference =============================================
# `make docs-api` regenerates the committed snapshot under docs/api/ from
# docstrings (pdoc) and JSDoc (typedoc). `make docs-api-check` regenerates to a
# temp dir and diffs against docs/api/, exiting non-zero on drift.
#
# STANDALONE drift gate, intentionally NOT wired into `checkall`: pdoc lives in
# the opt-in `docs` extra (not installed by `make install` or `uv sync --group
# dev`) and typedoc lives in the visualizer devDeps, so adding them to
# `checkall` would push a doc-generation toolchain onto every CI run. Run
# `make docs-api` after editing docstrings/JSDoc and commit the result.
#
# build_graph.py (numpy), summarize_sessions.py (anyio), and build_embeddings.py
# (fastembed/sqlite_vec guard) are skipped because their top-level imports are
# not satisfiable from the lightweight `docs` extra; html-to-md.py is a PEP 723
# script and not importable as a module (hyphen).
PDOC_MODULES := core installer vault_common vault_config vault_path vault_fs \
	vault_index vault_hooks vault_adaptive vault_metrics vault_tui vault_links \
	vault_new vault_constants vault_resolve vault_health subproc_util ai_backend \
	vault_embed_serve parmem_backend note_schema agent_adapter prompt_templates \
	session_start_hook session_stop_hook pre_compact_hook post_compact_hook \
	subagent_stop_hook codex_session_start_hook codex_stop_hook codex_subagent_stop_hook \
	gemini_session_start_hook gemini_session_end_hook vault_search \
	vault_review vault_export vault_merge vault_conflicts vault_doctor vault_stats \
	update_index check_graph_coverage run_trigger_eval

docs-api:
	$(MAKE) docs-api-gen DOCS_API_OUT=docs/api

docs-api-check:
	@tmp=$$(mktemp -d); \
	$(MAKE) docs-api-gen DOCS_API_OUT=$$tmp >/dev/null || { echo "docs-api generation failed" 1>&2; rm -rf $$tmp; exit 1; }; \
	if diff -r docs/api $$tmp >/dev/null; then \
		echo "docs/api is up to date"; rc=0; \
	else \
		echo "docs/api is stale -- run 'make docs-api' and commit the result" 1>&2; rc=1; \
	fi; \
	rm -rf $$tmp; exit $$rc

# Internal generator shared by docs-api and docs-api-check so the two cannot
# drift apart. Writes into $(DOCS_API_OUT). pdoc renders the runtime repr of
# module-level constants (e.g. installer.paths.REPO_ROOT, vault_path.VAULT_ROOT)
# which embed the checkout path and $HOME -- machine-specific values that would
# make the committed snapshot fail docs-api-check on any other machine or CI.
# pdoc has no flag to suppress default-value rendering and the offending
# constants derive from __file__/Path.home(), so we scrub the two machine-
# specific prefixes (repo root first, then home) to stable tokens. The result
# is byte-identical regardless of who runs it.
.PHONY: docs-api-gen
docs-api-gen:
	rm -rf $(abspath $(DOCS_API_OUT))
	PYTHONHASHSEED=0 PYTHONPATH=skills/parsidion/scripts:. uv run --extra docs python -m pdoc \
		-o $(abspath $(DOCS_API_OUT))/python $(PDOC_MODULES)
	cd visualizer && bunx typedoc --out $(abspath $(DOCS_API_OUT))/visualizer \
		--options typedoc.json
	find $(abspath $(DOCS_API_OUT)) -type f \( -name '*.html' -o -name '*.js' \) -print0 | \
		xargs -0 perl -pi -e 's|\Q$(CURDIR)\E|<repo-root>|g; s|\Q$(HOME)\E|<home>|g'

# Typecheck, lint, unit-test, and build the visualizer (bun)
# 'bun run build' catches RSC server/client boundary violations (ARC-041) that tsc --noEmit alone misses
visualizer-check:
	cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build

# Run all checks in sequence: format check, lint, typecheck, test
checkall: fmt-check lint typecheck test test-graph visualizer-check checkall-mcp

# Run parsidion-mcp checks (format, lint, typecheck, test)
checkall-mcp:
	$(MAKE) -C parsidion-mcp checkall

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
