---
description: Cut a release — bump the version everywhere, update CHANGELOG and README, run the release gate, commit, tag and push
argument-hint: "[version] (default: bump MINOR)"
---

# Cut a release

Target version: use `$ARGUMENTS` verbatim when it names a version (e.g. `0.28.0`); otherwise bump the MINOR of the current `pyproject.toml` version.

Work on the MAIN checkout, never an agent worktree (the docs/api generator bakes env-specific paths from worktrees).

## 1. Bump the version — all spots, synced to the target

- `pyproject.toml` — `version = "<target>"`
- `skills/parsidion/scripts/vault_common.py` — `__version__ = "<target>"`. This copy drifts (it has lagged `pyproject.toml` before); set it to the target even if its current value is behind.
- `uv.lock` — run `uv lock` so the lock records the new project version; the lock change goes into the release commit.

## 2. CHANGELOG.md (Keep a Changelog)

Turn the current `## [Unreleased]` heading into `## [<target>] - <YYYY-MM-DD>` and add a fresh empty `## [Unreleased]` above it. If `## [Unreleased]` is empty, derive the entries from `git log <last-tag>..HEAD --oneline` (last tag: `git tag --list 'v*' --sort=-v:refname | head -1`), grouped under `### Added` / `### Changed` / `### Fixed`, one concise bullet per user-visible change — match the density of existing entries.

## 3. README.md — two spots

- The `> **New in <old>:** ...` banner near the top: bump the number and summarize the headline change of this release in one sentence.
- The `Latest release: **<old>**` line in the Changelog section near the bottom: bump the number and its one-line description.

## 4. Release gate (before commit)

Run `make checkall-ci` from the repo root. If it fails on docs/api drift, run `make docs-api`, `git add docs/api`, and include the regenerated snapshot in the release commit, then re-run `make checkall-ci`. The release is not pushable until the gate is green — CI runs the same docs-api drift job and a stale snapshot fails the release push after the fact.

## 5. Commit, tag, push

1. Single atomic commit: `chore(release): <target>` containing pyproject.toml, vault_common.py, uv.lock, CHANGELOG.md, README.md (+ docs/api when step 4 regenerated it). No Co-Authored-By trailer.
2. Tag: `git tag v<target>`.
3. Push: `git push origin main v<target>` — invoking `/cut-release` is the explicit push approval; no need to ask again.
4. Watch the run to green (`gh run watch` or poll `gh run list --limit 1`) and report the result. If the docs-api drift job fails despite step 4, regenerate, commit `docs(api): regenerate snapshot for <target>`, re-tag the amended commit, and push again.

Report the released version, the commit SHA, the tag, and CI status when done.
