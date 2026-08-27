# ENH-024 — One stdlib mini-YAML module for the three hand-rolled dialects

## Goal

Consolidate the tree's three independent hand-rolled YAML subsets — the config parser (`core/vault_config.py:_parse_config_yaml`), the frontmatter parser (`core/vault_index.py:parse_frontmatter`), and the vaults.yaml reader/renderer (`core/vault_path.py`) — onto one shared, well-tested `core/yaml_lite.py` with a single set of quoting/comment/nesting rules, eliminating a class of "works in config.yaml but not in frontmatter" inconsistencies.

## Current state

- Three parsers evolved separately with separate quoting variants, comment handling, and nesting support (config: one nesting level + inline comments; frontmatter: inline arrays, quoted wikilinks; vaults.yaml: flat map with comment preservation). The audit flagged the trio via ARC-111/QA-104; QA-104 (this cycle) already rewrites `render_vaults_yaml` on a structured model.
- The stdlib-only rule forbids PyYAML on the hook path, so a shared subset module is the only consolidation option.
- Doctor incident history (tag rewrite quoting variants, frontmatter repair codes) shows the cost of divergent parsing rules landing on real vault files.

## Implementation

> Sequence AFTER QA-104 lands (its structured model becomes the vaults.yaml front-end of this module) and after the vaults.yaml `default:`-key backlog decision.

1. **Extract the common core.** Create `skills/parsidion/scripts/core/yaml_lite.py` with: scalar parsing (str/int/float/bool/null, quote stripping for `'`/`"`), inline `[a, b]` arrays, inline comment splitting (respecting quotes), one-level nested maps, and a `dump_scalar`/quoting policy — the union of what the three parsers genuinely support today, no more (this is a subset codification, not a YAML implementation).
2. **Adopt per consumer, behavior-pinned:**
   a. `_parse_config_yaml`: repoint tokenization/scalar handling at yaml_lite; the section/merge logic stays in `vault_config`. The existing config-parsing tests must pass unchanged.
   b. `parse_frontmatter`: same — array/quote/scalar handling from yaml_lite; frontmatter-specific structure (delimiters, `related` wikilink quoting) stays local. Differential test: parse every note in a fixture vault with old and new parsers, assert identical dicts (write the old parser's output to a fixture before switching).
   c. vaults.yaml (post-QA-104): its structured model uses yaml_lite for scalar/comment tokenization.
3. **Freeze the contract with tests.** A table-driven test module (`tests/test_yaml_lite.py`) covering every quoting/comment/array/nesting case, seeded from real-world lines harvested from the vault and templates (including the historical doctor false-positive shapes: TOML-ish lines, code-fence content).
4. **Stdlib gate.** `core/yaml_lite.py` sits inside the enforced stdlib scope automatically (core/); confirm `tests/test_stdlib_only.py` picks it up via its module enumeration.
5. **Docs.** A short module docstring stating the supported subset is the *only* YAML this project understands, and that new syntax must be added here (one place) with tests.

## Files to touch

- `skills/parsidion/scripts/core/yaml_lite.py` (new)
- `skills/parsidion/scripts/core/vault_config.py`, `core/vault_index.py`, `core/vault_path.py` (consumers)
- `tests/test_yaml_lite.py` (new), existing config/frontmatter/vaults tests (must pass unchanged)
- `docs/ARCHITECTURE.md` (one paragraph: the shared subset module)

## Verification

- Differential fixture test: identical parse output old-vs-new across the fixture corpus for config, frontmatter, and vaults.yaml inputs.
- All pre-existing tests green with zero edits to their assertions: `uv run pytest tests/ -k "config or frontmatter or vaults or yaml" -q`.
- `uv run pytest tests/test_stdlib_only.py -q` (module inside the gate).
- `make checkall` green.

## Rollback

Adoption is per-consumer and behavior-pinned; any consumer can be reverted to its private parser independently (keep the old functions in git history, not in-tree). Reverting the whole enhancement is three localized import/call reverts plus deleting the module.
