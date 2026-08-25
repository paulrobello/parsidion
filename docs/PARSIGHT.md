# parsight Code-Memory Backend

> **Status — parsight is not yet publicly available (coming soon).**
> The integration documented here is fully built and tested, and switches on
> automatically the moment parsight ships and you install it. Until then
> parsidion behaves **exactly as it would without parsight** — every feature
> works via the local embeddings fallback, and there is no parsight download to
> install today.

An **optional** integration: when the parsight code-memory system (local
install; see its own README for installation) is installed and its daemon
is running, parsidion's vault semantic search is served by parsight's
hybrid BM25+vector+graph retrieval instead of the local embeddings-only
cosine search. parsight absent means
parsidion behaves byte-for-byte as before — the local embeddings pipeline
(`embeddings.db`, `build_embeddings.py`) remains the always-on silent
fallback. The integration is pure stdlib (`shutil.which` + `subprocess` +
`urllib`), mirroring how parsidion already treats `mcpl` and `agentchrome`
as optional external CLIs.

## Table of Contents

- [What it adds](#what-it-adds)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Score semantics](#score-semantics)
- [Index freshness](#index-freshness)
- [Degradation matrix](#degradation-matrix)
- [Troubleshooting](#troubleshooting)
- [Graph & Visualization](#graph--visualization)
- [Graph enrichment](#graph-enrichment)
- [Related Documentation](#related-documentation)

## What it adds

- **Better vault search:** parsight's markdown indexing understands
  parsidion's note conventions — frontmatter `tags`/`type`/`project`/
  `confidence`/`date` become filterable metadata, `[[wikilinks]]`,
  `related:`, and `sources:` become graph edges, and notes become
  heading-section document nodes searched by hybrid retrieval. Everything
  downstream of `vault_search.py` (vault-explorer agent, parsidion-mcp
  `vault_search` tool, SessionStart context) inherits the upgrade for free.
- **Code-memory bridge:** the vault-explorer agent consults parsight's *code*
  graph for code-shaped questions (`agents/vault-explorer.md`, "Code-Memory
  Bridge" section), and parsidion-mcp exposes a `code_search` tool so Claude
  Desktop gets the same reach.
- **3D vault visualization:** see "Graph & Visualization" below.

## Requirements

parsidion's integration talks to parsight through its **CLI daemon-proxy
surface** — the daemon-proxied `repos`/`watch`/`unwatch`/`find-code`/`doc-links`
subcommands (plus the standalone `index` subcommand, which does not need the
daemon) — which requires a parsight build with **spec 15 (CLI daemon-proxy)
merged — 2026-07 or later**. A stock older
parsight binary fails gracefully: every probe or subprocess call errors or
exits nonzero, so parsidion falls straight back to embeddings, but the
integration provides no benefit until parsight is updated. There is no
partial/degraded proxy mode to opt into — either the daemon-proxy surface
is present, or parsight is treated as unavailable.

## Installation

parsidion never installs or requires parsight. To opt in (once parsight is
released — it is **not yet publicly available**; see the status note above):

1. Install the `parsight` CLI and start its daemon (an always-on launchd
   agent serving MCP over HTTP at `127.0.0.1:4848` — see the parsight
   project's own install docs).
2. Verify: `curl -sf http://127.0.0.1:4848/health` answers 200 and
   `parsight repos --json` lists indexed repositories.
3. Nothing else — `parsight.enabled: true` plus auto-detection means the
   upgrade activates on the next search. Without parsight the probe costs one
   cached `which` + a ~1 s health check per process, once.

## Configuration

```yaml
parsight:
  enabled: true        # use parsight when available; false = never probe
  binary: parsight      # PATH lookup or absolute path
  timeout_s: 10        # per-query subprocess timeout
search:
  backend: auto        # auto | parsight | embeddings | none
```

| Key | Default | Meaning |
|---|---|---|
| `parsight.enabled` | `true` | Master gate. `false` skips every probe and subprocess — today's parsidion exactly. |
| `parsight.binary` | `parsight` | CLI name resolved on `PATH`, or an absolute path. |
| `parsight.timeout_s` | `10` | Subprocess timeout per `find-code`/`repos` call; on expiry the query silently falls back to embeddings. |
| `search.backend` | `auto` | `auto` = parsight when available+indexed, embeddings otherwise (silent). `parsight` = parsight or empty, no fallback (debugging). `embeddings` = today's path unconditionally. `none` = semantic search disabled. |

The `vault-search` CLI accepts `--backend/-B` to override the config per
query, and `--rich` output names which backend served the query (on stderr).
The env var `PARSIGHT_MCP_URL` (default `http://127.0.0.1:4848/mcp`) points
both the parsight CLI and parsidion's health probe at the daemon.

### Backward compatibility (pre-rename configs)

parsight was formerly named par-mem; configs written before the rename keep
working without edits:

- A `par_mem:` config section is honored as an alias for `parsight:` — the
  canonical `parsight:` section wins per key when both are present, and the
  alias holds across the `config.local.yaml` overlay (a legacy `par_mem` key
  in the local overlay still overrides `config.yaml`).
- `search.backend: par-mem` (and `-B par-mem` on the CLI) selects `parsight`.
- When `parsight.binary` is unset, binary resolution prefers `parsight` on
  `PATH` and falls back to the legacy `par-mem` command when only that
  exists (no compat symlink required).

## Score semantics

parsight returns **RRF (reciprocal-rank-fusion) scores** — rank-fusion
values, typically small (≈0.01–0.10), NOT cosine similarities. The
`embeddings.min_score` config knob therefore applies to the
**embeddings backend only**; parsight results gate by rank and `top_k`.
Temporal decay (`embeddings.decay_*`) applies to both backends identically.
parsidion requests per-result RRF scores via `find-code --diagnostics`;
without them (older parsight, or a lane that omits one) each scoreless hit
gets a rank-preserving synthesized score (`1/(1+position)`) so it still
flows through aggregation/decay/sort instead of collapsing to a tie — the
final ordering therefore matches parsight's returned rank.

## Index freshness

- **Query-time:** each parsight-routed search checks `parsight repos --json`.
  A vault missing from it kicks a detached background `parsight index` and
  the current query falls back to embeddings (a later query picks parsight
  up); a *stale* vault also kicks a background reindex but the current
  query is still served from the stale-but-usable parsight index.
- **Rebuild trigger:** `update_index.py` ends its run by launching a
  background `parsight index` when the backend resolves (summarizer note
  writes call `rebuild_index()` and inherit this). NDJSON progress goes to
  `~/.claude/logs/parsidion-parsight.log`.
- **Live watch:** the SessionStart hook fire-and-forgets
  `parsight watch <vault> --hold-token parsidion-<session_id>`; SessionEnd
  releases it. Holds are refcounted with a server-side TTL, so a crashed
  session cannot leak one. Live edits from Obsidian/editors reindex without
  parsidion's involvement.
- **Residual:** if both miss (daemon was down), parsight's own
  startup-stale-refresh reconciles at daemon restart.

## Degradation matrix

| condition | behavior |
|---|---|
| `parsight.enabled: false` | no probe, no subprocess — today's parsidion exactly |
| binary missing / health probe fails | cached unavailable → embeddings path |
| vault not yet indexed in parsight | background index kicked; this query → embeddings |
| subprocess timeout / nonzero exit / garbage JSON | `write_hook_event` log → embeddings |
| parsight index job fails server-side | parsidion unaffected (embeddings keep serving) |
| parsight AND embeddings both unavailable | today's behavior: `[]` semantic, metadata still works |

No condition raises, blocks a hook past its timeout, or surfaces an error to
the agent/user. (The one deliberate exception: the parsidion-mcp
`code_search` tool raises a clear "parsight unavailable" error instead of
degrading silently, because MCP callers can choose another tool.)

## Troubleshooting

- **Is the backend being used?** `vault-search "query" --rich` prints
  `backend: parsight` or `backend: embeddings` on stderr. Force it with
  `--backend parsight` (returns `[]` rather than falling back).
- **Daemon down?** `curl -sf http://127.0.0.1:4848/health` — no answer means
  the launchd agent is not running; consult parsight's docs. parsidion keeps
  serving from embeddings meanwhile.
- **`/health` shows `ready: false, status: idle` right after a daemon
  restart?** Not an error — parsight's embedder warms lazily. It stays
  `idle` until the first query (from parsight or parsidion) triggers
  warm-up, then reports `ready: true` shortly after.
- **Daemon healthy but `parsight repos --json` still exits 2?** Parsight
  builds from 2026-07-12 or later route every daemon-required CLI call
  (`repos`/`watch`/`unwatch`, the query proxies) to the daemon from **any**
  working directory, including a git-repo vault like `~/ParsidionVault`
  that owns no local `.parsight` store of its own. Builds before that date
  only recognized a daemon anchored to the invoking directory's own store,
  so a fully healthy always-on daemon (`/health` 200) next to a vault with
  no local store still produced exit 2. If you see that exact combination,
  update parsight — it is not a parsidion-side issue.
- **Wrong parsight version?** `parsight repos --json` should exit 0 and list
  your vault. A nonzero exit, an "unknown subcommand" error, or a hang means
  the installed parsight predates the CLI daemon-proxy surface this
  integration requires (see "Requirements" above) — install or update
  parsight and restart its daemon before troubleshooting anything else.
- **Vault not indexed / stale?** `parsight repos --json` shows `root_path`
  plus per-worktree `indexed_head`/`stale`. Kick a manual index with
  `parsight index ~/ParsidionVault`. (`parsight status` only reports
  symbol/file counts — it carries no freshness information.)
- **`parsight index` exits 2 mentioning
  "queued behind another job's hold on the global index lock"?** Loud but
  harmless. This is parsidion's own background index trigger (see "Index
  freshness" above) hitting the daemon while it is busy serializing another
  index job — e.g. right after a daemon restart, while its own
  startup-stale-refresh reindexes every worktree. parsight self-heals the
  staleness on its own, and parsidion's next query falls back to embeddings
  meanwhile.
- **Background index/watch output:** `~/.claude/logs/parsidion-parsight.log`
  (NDJSON progress events and watch/unwatch output).
- **Backend failures:** logged to `<vault>/hook_events.log` as
  `"hook": "ParsightBackend"` entries (`vault-stats --hooks 20` surfaces them).
- **CLI exit codes:** 0 ok, 1 failed, 2 daemon-unreachable, 3 owner-lock
  conflict (`parsight index` run without the daemon while another process
  owns the store), 4 wait-timeout. parsidion treats every nonzero exit as
  "fall back to embeddings".
- **Custom daemon URL:** set `PARSIGHT_MCP_URL` (it passes through to child
  processes via parsidion's safe-env allowlist).

## Graph & Visualization

Once the vault is indexed, parsight's built-in real-time 3D visualizer works
over it with **zero parsidion changes**:

```bash
parsight ui        # prints/opens the daemon's UI endpoint
```

The visualizer renders the vault as a force-directed 3D knowledge graph —
notes as heading-section document nodes, `[[wikilinks]]` / `related:` /
`sources:` frontmatter and in-body markdown links as edges — with live
updates while a `parsight watch` hold is active (parsidion's SessionStart
hook holds one automatically). It complements, rather than replaces,
parsidion's own Sigma.js visualizer (see [docs/VISUALIZER.md](VISUALIZER.md)).

**Parsidion's `graph.json` stays local.** The Sigma.js visualizer's
`graph.json` build (`build_graph.py`) keeps using `embeddings.db` for its
semantic-similarity edges — which independence preserves anyway — and
parsidion-derived wiki edges for structure.

**Shipped:** merging parsight's in-body markdown-link edges — which
parsidion's `related:`-frontmatter-only wiki edges miss — into `graph.json`
as extra `kind: "wiki"` edges. See "Graph enrichment" below.

## Graph enrichment

`build_graph.py` optionally enriches its `kind: "wiki"` edges with parsight's
in-body doc links. Frontmatter `related:` parsing (the always-on source)
only sees links declared in a note's frontmatter; parsight's markdown
indexer additionally extracts `[[wikilinks]]` and markdown links written in
a note's body, which `build_graph.py` cannot see on its own.

On every run (unless `--no-parsight` is passed), `build_graph.py` calls
`resolve_parsight_backend()` — the same availability probe search uses (config
gate + binary on `PATH` + daemon `/health`; see "Troubleshooting" below) —
then checks the parsight index is **fresh** for the vault (see "Freshness"
below) before it runs:

```bash
parsight doc-links --json --targets doc --limit 200000
```

against the vault. Each returned `source_path`/`target_path` pair that
resolves to two distinct, known note stems becomes an extra
`{"s", "t", "w": 1.0, "kind": "wiki"}` edge, deduplicated against the
frontmatter-derived wiki edges (and against itself). When one or more edges
were added, the output `graph.json`'s `meta` gains a `parsight_body_links`
count; when none were added the key is omitted entirely (not written as
zero). Whenever enrichment was attempted (i.e. `--no-parsight` was *not*
passed) `meta` also carries a `parsight_body_status` string recording the
outcome — `fresh` (ran cleanly), `skipped:index-stale` /
`skipped:index-absent` / `skipped:index-invalid` (index not fresh,
enrichment skipped), or `unavailable` / `error` (backend failure). When
`--no-parsight` was passed, neither key is present and the `nodes`/`edges`
content matches the pre-integration output.

- **Opt out:** `build_graph.py --no-parsight` skips the enrichment
  unconditionally.
- **Troubleshooting:** enrichment silently contributes zero edges whenever
  the standard availability probe fails — same probe as search; see
  "Troubleshooting" above for how to diagnose it.
- **Freshness (determinism gate):** a stale or mid-catch-up index returns a
  partial, run-to-run-variable link set, which would make two `graph.json`
  builds over identical input diverge. So before trusting body links,
  `build_graph.py` asks `parsight repos --json` whether the vault's index is
  current (`parsight_backend.vault_index_fresh`); when it is not fresh the
  `doc-links` fetch is skipped entirely and `meta.parsight_body_status`
  records `skipped:index-stale` (or `-absent` / `-invalid`). The build stays
  deterministic regardless of index state — re-run after the background
  reindex completes to pick up body links. This probe is side-effect-free: it
  never spawns a reindex itself (unlike the search path's
  `ensure_vault_indexed`, which kicks one on stale). A body link written
  moments before a rebuild may still not appear until the next one, since the
  background reindex and the graph build are decoupled by design.

The visualizer surfaces the integration three ways: the `?` search prefix runs
semantic search through `vault_search.py` (parsight's warm daemon when enabled,
embeddings fallback otherwise), the Reading Pane's **Linked Notes** section is
completed by the in-body links this enrichment contributes, and the graph HUD's
Graph Analysis panel shows a `body links` chip when `graph.json` carries
`meta.parsight_body_links`.

## Related Documentation

- [README.md](../README.md) — project overview and optional external tools
- [docs/EMBEDDINGS.md](EMBEDDINGS.md) — the local embeddings pipeline (the fallback)
- [docs/MCP.md](MCP.md) — parsidion-mcp server, including the `code_search` tool
- [docs/archive/MCPL.md](archive/MCPL.md) — the same optional-external-CLI integration pattern (legacy reference, not installed)
