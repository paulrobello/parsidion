.PHONY: install lint lint-fix fmt fmt-check typecheck test checkall fix build

build:
	@echo "No build step needed — editable install only."

install:
	uv tool install --editable .

fmt:
	uv run ruff format .

# Verify formatting without rewriting files
fmt-check:
	uv run ruff format --check .

# Lint without applying fixes (non-mutating)
lint:
	uv run ruff check .

# Lint and apply auto-fixes (mutating — opt-in convenience)
lint-fix:
	uv run ruff check --fix .

typecheck:
	uv run pyright .

test:
	uv run pytest

# Non-mutating gate (used by root make checkall)
checkall: fmt-check lint typecheck test

# Mutating convenience target for local development
fix: fmt lint-fix
