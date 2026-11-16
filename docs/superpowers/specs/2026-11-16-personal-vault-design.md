# Personal Vault — Design

Date: 2026-11-16
Status: approved — Paul chose bootstrap option (a) on 2026-11-16; vault lives at
`~/ParsidionPersonalVault` (registered name `personal`); dedicated agent folder is
`~/Repos/personal`.

## Intent and why

Parsidion vaults today hold engineering memory. Paul also wants a personal-assistant
knowledge base — home, family, pets, appliances, vehicles, health, daily-life reference —
writable and readable by a coding agent, but **never mixed into coding sessions**. The
usage pattern is explicit: a dedicated project folder (`~/Repos/personal`) from which an
agent runs with the personal vault resolved as *its* vault. Work sessions keep resolving
the main vault and must see nothing personal.

Why a separate vault rather than a folder: vault resolution already supports per-cwd
override (`<cwd>/.claude/vault` file), each vault carries its own index, embeddings,
daily notes, pending queue, and git — isolation is a property of the mechanism, not code
we write. The missing pieces are (1) the engineering taxonomy is hardcoded into every
vault, (2) scheduled maintenance only touches the default vault.

## Goals

1. A second vault (`~/ParsidionPersonalVault`, registered as named vault `personal`)
   usable as a personal KB: agent sessions run from `~/Repos/personal` and resolve it.
2. Per-vault folder taxonomy driven by that vault's `config.yaml`, so the personal vault
   gets folders like `Home`, `Family`, `Pets`, `Appliances`, `Vehicles`, `Health`,
   `Finance`, `Reference` instead of `Debugging`/`Patterns`/`Frameworks`.
3. Nightly scheduled maintenance (summarizer + doctor + graph rebuild) covers **all
   registered vaults**, not just the default.
4. Visualizer and `vault-search` treat the personal vault as a first-class target.

## Non-goals

- No gating/exclusion logic in the main vault (isolation comes from separate vaults).
- No new note *types* — the standard vocabulary (`knowledge`, `debugging`, `tool`, …)
  stays global; folders and tags carry the personal domain (`pet`, `appliance`,
  `family`).
- No cross-vault recall: a work session cannot query personal notes, by design.
- No remote sync setup for the personal vault — local git only unless a remote is added
  manually later.

## Anti-goals

- Personal content appearing in any work-session context injection, summarizer prompt,
  or the main vault's index/embeddings. If a test shows leakage, the design fails.
- Secrets (alarm codes, SSNs, passwords) belong in parvault, never in any vault note.
  Docs for this feature say so explicitly.

## Constraints (verified in code)

- `parsidion/core/vault_path.py`: `VAULT_DIRS` is a module constant consumed by
  `ensure_vault_structure()` in `parsidion/core/vault_fs.py` (creates every standard
  folder in any vault) and by the installer (`get_vault_dirs()` in
  `installer/paths.py`, with a regex fallback copy).
- `parsidion/note_schema.py`: `TYPE_FOLDERS` (type→folder), `DEFAULT_FOLDER`
  ("Research"), `VALID_NOTE_TYPES` — pure vault-agnostic constants.
- Consumers of `TYPE_FOLDERS`: `vault_new.py` (`--type` choices + routing),
  `summarizer/notes.py` (rejects notes whose type is not in `VALID_NOTE_TYPES`; routes
  by type→folder), and `doctor` (alias in `doctor/_state.py`; `doctor/check.py` flags
  unknown types). Types stay standard, so doctor/summarizer *type* logic is untouched.
- The indexer and vault traversal walk **all** folders except `EXCLUDE_DIRS` — arbitrary
  folder names are already indexed and searchable. Only creation/routing assume the
  standard set.
- Config parsing (`core/vault_config.py:_parse_config_yaml`) and the typed schema
  (`core/vault_schema.py`) are scalar-only: every value becomes a bare string, and the
  schema's leaves are scalars. Lists and nested maps cannot be represented, so the
  taxonomy section uses scalar encoding (comma-separated `folders`, dotted
  `type_folder.<type>` keys). In the typed schema the section carries its two fixed
  scalar fields (`folders`, `default_folder`); the dotted routing keys are an
  open-ended convention documented in the template and `docs/generated/config-reference.md`,
  not enumerated keys. `make config-docs` regeneration then satisfies the drift gate.
- Scheduled job (`installer/schedule.py`): one launchd/cron entry invoking
  `summarize_sessions.py --run-doctor` (optionally `--rebuild-graph`) with **no vault
  argument** → default vault only.
- Visualizer (`visualizer/lib/vaultResolver.ts`): `resolveVault(vaultName?)`,
  `listNamedVaults()`, `/api/vaults` route already exist; the personal vault registered
  in `vaults.yaml` should appear in the switcher with no resolver changes (verify UI
  wiring + graph/endpoint vault-agnosticism).
- `vault-search` already accepts `-V PATH|NAME`; folder filters query `note_index`
  (folder-agnostic).
- stdlib-only gate (`tests/test_stdlib_only.py`) covers everything touched here.
- Vault-resolution contract fixture (`tests/fixtures/parity/vault-resolution.json`) must
  not change — this design adds no new resolution channel.

## Approaches considered

**Isolation — how personal content stays out of coding sessions:**

- *A. Separate personal vault* (chosen): dedicated folder + `.claude/vault` file →
  second vault with own index/embeddings/git. Zero leakage by construction; near-zero
  gating code. Cost: no cross-domain recall (matches stated usage).
- *B. `Personal/` folder in the main vault with injection-exclusion config*: one vault,
  cross-recall possible when explicit — but requires gating logic in 3+ hooks, still
  shares embeddings db and git remote with work data. More code, weaker isolation.
- *C. Ungated folder*: rejected outright — session-start injects a whole-vault index.

**Taxonomy — how the personal vault gets its own folders:**

- *1. Config-driven per-vault taxonomy* (chosen): `taxonomy:` section in each vault's
  `config.yaml`; built-in defaults when unset. One mechanism, user-reshapable, existing
  installs unchanged.
- *2. Built-in `personal` profile flag*: hardcoded second set; any folder addition means
  editing source. Rejected.
- *3. Free-form folders only*: minimal code, but `ensure_vault_structure` still creates
  engineering folders and the summarizer still routes into them. Rejected.

## The design

### 1. Taxonomy config + resolution

`config.yaml` (per vault), all optional. The config system is scalar-only by design
(`_parse_config_yaml` returns bare strings; the typed schema's leaves are scalars), so
the taxonomy uses scalar encoding — comma-separated folder list and dotted routing keys
— instead of YAML lists/maps. This keeps the parser, `vault_schema.py`, and the
`config-docs` drift gate untouched:

```yaml
taxonomy:
  folders: Home, Family, Pets, Appliances, Vehicles, Health, Finance, Reference
  type_folder.knowledge: Reference
  type_folder.debugging: Home      # troubleshooting & repairs
  type_folder.tool: Appliances     # manuals, products
  default_folder: Reference
```

New resolver — `resolve_taxonomy(vault) -> Taxonomy(folders, type_folders,
default_folder)` — lives in `parsidion.core.vault_path` (it needs vault context and
config access; `note_schema.py` keeps the built-in constants as defaults). Semantics:

- Config absent → exactly today's `VAULT_DIRS` / `TYPE_FOLDERS` / `DEFAULT_FOLDER`.
  Existing vaults are byte-for-byte unchanged in behavior.
- `type_folders` overrides routing only for the types it lists; unlisted types keep the
  built-in folder **if** that folder is in the vault's folder list, else `default_folder`.
- `folders` drives what `ensure_vault_structure` and the installer create; standard
  folders not in the list are simply never created for that vault. `Daily`,
  `Templates`, `History` stay always-on (system folders, not taxonomy).
- `VALID_NOTE_TYPES` unchanged; `vault-new --type` choices unchanged; only the routing
  target differs per vault.

Consumers switch from the constants to `resolve_taxonomy(resolved_vault)`:
`ensure_vault_structure`, installer `create_vault_dirs`, `vault_new`, summarizer note
routing, and `_launch_summarizer_if_pending()` in `agent_pipeline.py` (the session-end
auto-summarizer spawn currently passes no vault argument and relies on inherited cwd —
it must pass the resolved vault explicitly via `-V`, which the Codex adapter's missing
cwd would otherwise break). `installer/paths.py`'s fallback copy keeps the built-in
list (it runs before any vault config exists).

When a taxonomy is set, `ensure_vault_structure` also prunes **empty** folders that are
neither in the taxonomy list nor system folders — so a vault whose standard folders
were created before its taxonomy existed (installer runs before config is written)
converges on its own structure. Only empty directories are ever removed; a folder
containing notes is never touched.

### 2. Bootstrap

**Decision (Paul, 2026-11-16): option (a).** `record_installed_vault()` deliberately
writes any `--vault` install as the `vaults.yaml` `default:` key (ARC-019), so the
naive recipe below would make the personal vault the default for every work session —
the exact anti-goal. Chosen repair:

- `install.py` gains `--no-set-default` (registers the vault as named only) and
  `--vault-name` (the named entry defaults to the directory stem —
  `ParsidionPersonalVault`, not `personal`, and a `.claude/vault` file naming an
  unregistered name is silently skipped by the resolver today). Adding a second vault
  becomes a first-class, safe operation.

Also in scope: an unresolvable `.claude/vault` reference must stop being silent.
Resolution failure on that channel emits a visible warning through the hook's output
(and a `hook_events.log` entry) instead of quietly falling through to the next channel
— silent fall-through is precisely the cross-vault leakage mode. The vault-resolution
parity fixture gains a vector documenting this behavior.

Recipe:

1. `uv run install.py --vault ~/ParsidionPersonalVault --vault-name personal
   --no-set-default --vault-username <name>` — creates structure, git, post-merge
   hook, and registers the named vault without touching `default:`.
2. Add the `taxonomy:` block to the vault's `config.yaml` (shipped as a commented
   example in `templates/config.yaml`, regenerated via `make config-docs`); empty
   engineering folders created in step 1 are pruned on the next
   `ensure_vault_structure` pass.
3. `mkdir -p ~/Repos/personal/.claude && printf 'personal\n' >
   ~/Repos/personal/.claude/vault`.
4. Agent sessions launched in `~/Repos/personal` resolve the personal vault for all
   hooks, search, and summarization.

### 3. Scheduled maintenance across vaults

`summarize_sessions.py` gains `--all-vaults`: enumerate registered vaults (default +
named from `vaults.yaml`), process each vault's pending queue / doctor / graph rebuild
with `-V`. `schedule.py` passes `--all-vaults` on new scheduled installs (existing jobs
opt in by re-running `--schedule-summarizer`). Chosen over per-vault cron lines so
adding a vault never touches the schedule.

### 4. Visualizer, search, stats

- Visualizer: verify the vault switcher surfaces the named vault and that graph/notes
  endpoints are folder-agnostic. Expected: no code change beyond tests.
- `vault-search` / `vault-stats`: already `-V`-capable; add regression coverage that
  `-V personal` reflects taxonomy folders in metadata mode.

## Success criteria

1. Session run from `~/Repos/personal`: session-start index, prompt recall, daily
   note, and summarizer output all land in the personal vault; a work-repo session
   receives zero personal content.
2. `vault-new -V ~/ParsidionPersonalVault --type knowledge` scaffolds into
   `Reference/`.
3. Summarizer run with a personal-vault session writes a note routed by the vault's
   `type_folder.*` keys, and daily notes under `Daily/YYYY-MM/`; the session-end
   auto-summarizer spawn passes the resolved vault explicitly.
4. Nightly `--all-vaults` run processes both vaults' queues (test with two temp vaults).
5. Visualizer switcher lists both vaults; personal graph renders its taxonomy folders.
6. `make checkall` green, including `config-docs-check` (taxonomy documented) and the
   stdlib gate.
7. A `.claude/vault` file naming an unresolvable vault produces a visible warning and a
   `hook_events.log` entry — never a silent fall-through to another vault. The
   vault-resolution parity fixture gains a vector pinning this.

## Left to the builder

- `resolve_taxonomy` exact placement and signature (vault-path module vs new helper in
  note_schema taking resolved config — avoid import cycles).
- Implementing the chosen bootstrap option (a) or (b) from the decision above.
- Where `--all-vaults` enumeration lives (reuse `vault_path` vaults.yaml reader).
- Visualizer verification scope (switcher + endpoints) and any small fixes found.

## Calls made without asking (check these)

- Standard note types stay global; only folder routing is per-vault.
- `Daily` / `Templates` / `History` are system folders outside taxonomy control.
- Taxonomy config is scalar-encoded (comma list + dotted keys) — no list/map machinery
  added to the config parser or typed schema.
- When a taxonomy is set, empty non-taxonomy folders are pruned (never non-empty ones).
- The session-end auto-summarizer spawn passes the resolved vault explicitly (`-V`).
- An unresolvable `.claude/vault` reference warns visibly instead of falling through
  silently (parity fixture updated to pin the behavior).
- Scheduler uses one job with `--all-vaults` (runtime enumeration), not per-vault jobs.
- Personal vault gets local git only; no remote is configured by this work.
