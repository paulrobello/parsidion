.PHONY: build test test-graph lint fmt fmt-check typecheck checkall checkall-mcp clean install graph graph-with-daily visualizer stop-visualizer build-visualizer visualizer-setup

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
	uv run --with numpy pytest tests/test_build_graph_parmem.py

# Run all checks in sequence: format check, lint, typecheck, test
checkall: fmt-check lint typecheck test test-graph checkall-mcp

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
