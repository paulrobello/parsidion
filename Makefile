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

# Run build_graph.py's parsight body-link enrichment tests (numpy-gated,
# skipped under `test`/`checkall`'s numpy-free default suite)
test-graph:
	uv run --with numpy python -c "import numpy" && uv run --with numpy pytest tests/test_build_graph_parsight.py

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
	vault_embed_serve parsight_backend note_schema agent_adapter prompt_templates \
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
# pdoc has no flag to suppress default-value rendering, and the machine paths
# leak into MORE than the visible value text: pdoc's lunr search index stores
# default_value TOKEN COUNTS (field lengths) and a per-character inverted-index
# TRIE, both built from the pre-scrubbed strings -- neither reachable by a
# post-hoc text scrub. pdoc also decides whether to emit its "view-value
# toggle" widget and its condensed-vs-multiline signature layout from
# PRE-rendered lengths, so the same source can render differently on machines
# whose checkout path (or python minor) differs.
#
# The only airtight fix is to make the generator's INPUT machine-independent:
# docs-api-gen rsyncs the documented packages to a fixed physical path
# (PDOC_GEN_ROOT -- a real directory, not a symlink, so constants that call
# .resolve() themselves, like installer.paths.REPO_ROOT, still land on the
# fixed path) and runs pdoc with HOME pointed at a fixed empty directory (for
# Path.home()-derived constants like VAULT_ROOT/TEMPLATES_DIR). Every
# __file__- and home-derived repr, trie token, and field length is then
# identical on every machine and in CI; the scrub below merely rewrites the
# fixed paths to readable <repo-root>/<home> tokens. rsync copies only .py
# files, so no build artifacts travel. Note that typedoc's GitHub source links
# still require a normal (non-worktree) checkout -- typedoc cannot resolve the
# git remote through a worktree's .git file indirection -- so run make
# docs-api from the main checkout, not an agent worktree.
#
# Two further normalizations:
#
# - pdoc must run on the SAME python minor as CI: its condensed-vs-multiline
#   signature layout is a length comparison against the stringified
#   parameter/annotation reprs, which differ between python minors (e.g. 3.13
#   vs 3.14 type display), flipping borderline signatures. The docs job in
#   .github/workflows/ci.yml runs python 3.13, so the pin below keeps every
#   environment rendering CI's layout; --isolated keeps the pinned interpreter
#   in an ephemeral cache instead of re-creating the project .venv on machines
#   whose local default is a newer minor.
#
# - set/frozenset default-value reprs iterate in hash order: under
#   PYTHONHASHSEED=0 the order is stable for a given interpreter build, but
#   the first pdoc run in some freshly-built uv environments has been
#   observed with a different (non-seed-0) order, and a plain PYTHONHASHSEED
#   cannot guarantee cross-build stability. The scrub therefore sorts the
#   elements of every frozenset({...}) display and of every colon-free brace
#   group made purely of quoted elements (dict displays contain colons and
#   keep their insertion order), making the emitted order canonical. It also
#   strips the cosmetic view-value toggle markup and pins every numeric
#   default_value lunr field length to 1, so those cannot vary either.
.PHONY: docs-api-gen
PDOC_GEN_ROOT := /tmp/parsidion-docs-gen
PDOC_GEN_HOME := /tmp/parsidion-docs-home
docs-api-gen:
	rm -rf $(PDOC_GEN_ROOT) $(PDOC_GEN_HOME)
	mkdir -p $(PDOC_GEN_ROOT)/skills/parsidion $(PDOC_GEN_HOME)
	rsync -a --include='*/' --include='*.py' --exclude='*' \
		"$(CURDIR)/installer" $(PDOC_GEN_ROOT)/
	rsync -a --include='*/' --include='*.py' --exclude='*' \
		"$(CURDIR)/skills/parsidion/scripts" $(PDOC_GEN_ROOT)/skills/parsidion/
	rm -rf $(abspath $(DOCS_API_OUT))
	PYTHONHASHSEED=0 HOME=$(PDOC_GEN_HOME) \
		PYTHONPATH=$(PDOC_GEN_ROOT)/skills/parsidion/scripts:$(PDOC_GEN_ROOT) \
		uv run --isolated --python 3.13 --extra docs python -P -m pdoc \
		-o $(abspath $(DOCS_API_OUT))/python $(PDOC_MODULES)
	cd visualizer && bunx typedoc --out $(abspath $(DOCS_API_OUT))/visualizer \
		--options typedoc.json
	GEN_RESOLVED=$$(realpath $(PDOC_GEN_ROOT) 2>/dev/null || echo $(PDOC_GEN_ROOT)) \
		HOME_RESOLVED=$$(realpath $(PDOC_GEN_HOME) 2>/dev/null || echo $(PDOC_GEN_HOME)) \
		find $(abspath $(DOCS_API_OUT)) -type f \( -name '*.html' -o -name '*.js' \) -print0 | \
		xargs -0 perl -pi -e '$$ENV{GEN_RESOLVED} ne "" && s|\Q$$ENV{GEN_RESOLVED}\E|<repo-root>|g; s|\Q$(PDOC_GEN_ROOT)\E|<repo-root>|g; s|\Q$(CURDIR)\E|<repo-root>|g; $$ENV{HOME_RESOLVED} ne "" && s|\Q$$ENV{HOME_RESOLVED}\E|<home>|g; s|\Q$(PDOC_GEN_HOME)\E|<home>|g; s|\Q$(HOME)\E|<home>|g; s/<input id="[^"]*view-value" class="view-value-toggle-state"[^>]*>\s*//g; s/<label class="view-value-button pdoc-button" for="[^"]*"><\/label>//g; s/"default_value": \d+/"default_value": 1/g; s!\bfrozenset\(\{([^{}]+)\}\)!do { my $$i=$$1; "frozenset({".join(", ", sort(split(/,\s+/, $$i)))."})" }!ge; s!\{((?:\x27|\&#39;|\&#x27;)[^{}()]*?(?:\x27|\&#39;|\&#x27;)(?:,\s*(?:\x27|\&#39;|\&#x27;)[^{}()]*?(?:\x27|\&#39;|\&#x27;))*)\}!do { my $$g=$$1; $$g =~ /:/ ? "{".$$g."}" : "{".join(", ", sort(split(/,\s+/, $$g)))."}" }!ge'

# Typecheck, lint, unit-test, and build the visualizer (bun)
# 'bun run build' catches RSC server/client boundary violations (ARC-041) that tsc --noEmit alone misses
visualizer-check:
	cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build

# Run all checks in sequence: format check, lint, typecheck, test
checkall: fmt-check lint typecheck test test-graph visualizer-check checkall-mcp config-docs-check

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
