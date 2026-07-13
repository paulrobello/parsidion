# par-mem Code-Memory Backend

An **optional** integration: when the par-mem code-memory system (local
install; see its own README for installation) is installed and its daemon
is running, parsidion's vault semantic search is served by par-mem's
hybrid BM25+vector+graph retrieval instead of the local embeddings-only
cosine search. par-mem absent means
parsidion behaves byte-for-byte as before — the local embeddings pipeline
(`embeddings.db`, `build_embeddings.py`) remains the always-on silent
fallback. The integration is pure stdlib (`shutil.which` + `subprocess` +
`urllib`), mirroring how parsidion already treats `mcpl` and `agentchrome`
as optional external CLIs.

## What it adds

- **Better vault search:** par-mem's markdown indexing understands
  parsidion's note conventions — frontmatter `tags`/`type`/`project`/
  `confidence`/`date` become filterable metadata, `[[wikilinks]]`,
  `related:`, and `sources:` become graph edges, and notes become
  heading-section document nodes searched by hybrid retrieval. Everything
  downstream of `vault_search.py` (vault-explorer agent, parsidion-mcp
  `vault_search` tool, SessionStart context) inherits the upgrade for free.
- **Code-memory bridge:** the vault-explorer agent consults par-mem's *code*
  graph for code-shaped questions (`agents/vault-explorer.md`, "Code-Memory
  Bridge" section), and parsidion-mcp exposes a `code_search` tool so Claude
  Desktop gets the same reach.
- **3D vault visualization:** see "Graph & Visualization" below.

## Requirements

parsidion's integration talks to par-mem exclusively through its **CLI
daemon-proxy surface** — the `repos`/`watch`/`unwatch` subcommands and
daemon-proxied `find-code --json` — which requires a par-mem build with
**spec 15 (CLI daemon-proxy) merged — 2026-07 or later**. A stock older
par-mem binary fails gracefully: every probe or subprocess call errors or
exits nonzero, so parsidion falls straight back to embeddings, but the
integration provides no benefit until par-mem is updated. There is no
partial/degraded proxy mode to opt into — either the daemon-proxy surface
is present, or par-mem is treated as unavailable.

## Installation

parsidion never installs or requires par-mem. To opt in:

1. Install the `par-mem` CLI and start its daemon (an always-on launchd
   agent serving MCP over HTTP at `127.0.0.1:4848` — see the par-mem
   project's own install docs).
2. Verify: `curl -sf http://127.0.0.1:4848/health` answers 200 and
   `par-mem repos --json` lists indexed repositories.
3. Nothing else — `par_mem.enabled: true` plus auto-detection means the
   upgrade activates on the next search. Without par-mem the probe costs one
   cached `which` + a ~1 s health check per process, once.

## Configuration

```yaml
par_mem:
  enabled: true        # use par-mem when available; false = never probe
  binary: par-mem      # PATH lookup or absolute path
  timeout_s: 10        # per-query subprocess timeout
search:
  backend: auto        # auto | par-mem | embeddings | none
```

| Key | Default | Meaning |
|---|---|---|
| `par_mem.enabled` | `true` | Master gate. `false` skips every probe and subprocess — today's parsidion exactly. |
| `par_mem.binary` | `par-mem` | CLI name resolved on `PATH`, or an absolute path. |
| `par_mem.timeout_s` | `10` | Subprocess timeout per `find-code`/`repos` call; on expiry the query silently falls back to embeddings. |
| `search.backend` | `auto` | `auto` = par-mem when available+indexed, embeddings otherwise (silent). `par-mem` = par-mem or empty, no fallback (debugging). `embeddings` = today's path unconditionally. `none` = semantic search disabled. |

The `vault-search` CLI accepts `--backend/-B` to override the config per
query, and `--rich` output names which backend served the query (on stderr).
The env var `PARMEM_MCP_URL` (default `http://127.0.0.1:4848/mcp`) points
both the par-mem CLI and parsidion's health probe at the daemon.

## Score semantics

par-mem returns **RRF (reciprocal-rank-fusion) scores** — rank-fusion
values, typically small (≈0.01–0.10), NOT cosine similarities. The
`embeddings.min_score` config knob therefore applies to the
**embeddings backend only**; par-mem results gate by rank and `top_k`.
Temporal decay (`embeddings.decay_*`) applies to both backends identically.
parsidion requests per-result RRF scores via `find-code --diagnostics`;
without them (older par-mem), ordering falls back to par-mem's returned
rank.

## Index freshness

- **Query-time:** each par-mem-routed search checks `par-mem repos --json`.
  A vault missing from it kicks a detached background `par-mem index` and
  the current query falls back to embeddings (a later query picks par-mem
  up); a *stale* vault also kicks a background reindex but the current
  query is still served from the stale-but-usable par-mem index.
- **Rebuild trigger:** `update_index.py` ends its run by launching a
  background `par-mem index` when the backend resolves (summarizer note
  writes call `rebuild_index()` and inherit this). NDJSON progress goes to
  `~/.claude/logs/parsidion-parmem.log`.
- **Live watch:** the SessionStart hook fire-and-forgets
  `par-mem watch <vault> --hold-token parsidion-<session_id>`; SessionEnd
  releases it. Holds are refcounted with a server-side TTL, so a crashed
  session cannot leak one. Live edits from Obsidian/editors reindex without
  parsidion's involvement.
- **Residual:** if both miss (daemon was down), par-mem's own
  startup-stale-refresh reconciles at daemon restart.

## Degradation matrix

| condition | behavior |
|---|---|
| `par_mem.enabled: false` | no probe, no subprocess — today's parsidion exactly |
| binary missing / health probe fails | cached unavailable → embeddings path |
| vault not yet indexed in par-mem | background index kicked; this query → embeddings |
| subprocess timeout / nonzero exit / garbage JSON | `write_hook_event` log → embeddings |
| par-mem index job fails server-side | parsidion unaffected (embeddings keep serving) |
| par-mem AND embeddings both unavailable | today's behavior: `[]` semantic, metadata still works |

No condition raises, blocks a hook past its timeout, or surfaces an error to
the agent/user. (The one deliberate exception: the parsidion-mcp
`code_search` tool raises a clear "par-mem unavailable" error instead of
degrading silently, because MCP callers can choose another tool.)

## Troubleshooting

- **Is the backend being used?** `vault-search "query" --rich` prints
  `backend: par-mem` or `backend: embeddings` on stderr. Force it with
  `--backend par-mem` (returns `[]` rather than falling back).
- **Daemon down?** `curl -sf http://127.0.0.1:4848/health` — no answer means
  the launchd agent is not running; consult par-mem's docs. parsidion keeps
  serving from embeddings meanwhile.
- **`/health` shows `ready: false, status: idle` right after a daemon
  restart?** Not an error — par-mem's embedder warms lazily. It stays
  `idle` until the first query (from par-mem or parsidion) triggers
  warm-up, then reports `ready: true` shortly after.
- **Daemon healthy but `par-mem repos --json` still exits 2?** Par-mem
  builds from 2026-07-12 or later route every daemon-required CLI call
  (`repos`/`watch`/`unwatch`, the query proxies) to the daemon from **any**
  working directory, including a git-repo vault like `~/ParsidionVault`
  that owns no local `.parmem` store of its own. Builds before that date
  only recognized a daemon anchored to the invoking directory's own store,
  so a fully healthy always-on daemon (`/health` 200) next to a vault with
  no local store still produced exit 2. If you see that exact combination,
  update par-mem — it is not a parsidion-side issue.
- **Wrong par-mem version?** `par-mem repos --json` should exit 0 and list
  your vault. A nonzero exit, an "unknown subcommand" error, or a hang means
  the installed par-mem predates the CLI daemon-proxy surface this
  integration requires (see "Requirements" above) — install or update
  par-mem and restart its daemon before troubleshooting anything else.
- **Vault not indexed / stale?** `par-mem repos --json` shows `root_path`
  plus per-worktree `indexed_head`/`stale`. Kick a manual index with
  `par-mem index ~/ParsidionVault`. (`par-mem status` only reports
  symbol/file counts — it carries no freshness information.)
- **`par-mem index` exits 2 mentioning
  "queued behind another job's hold on the global index lock"?** Loud but
  harmless. This is parsidion's own background index trigger (see "Index
  freshness" above) hitting the daemon while it is busy serializing another
  index job — e.g. right after a daemon restart, while its own
  startup-stale-refresh reindexes every worktree. par-mem self-heals the
  staleness on its own, and parsidion's next query falls back to embeddings
  meanwhile.
- **Background index/watch output:** `~/.claude/logs/parsidion-parmem.log`
  (NDJSON progress events and watch/unwatch output).
- **Backend failures:** logged to `<vault>/hook_events.log` as
  `"hook": "ParMemBackend"` entries (`vault-stats --hooks 20` surfaces them).
- **CLI exit codes:** 0 ok, 1 failed, 2 daemon-unreachable, 3 owner-lock
  conflict (`par-mem index` run without the daemon while another process
  owns the store), 4 wait-timeout. parsidion treats every nonzero exit as
  "fall back to embeddings".
- **Custom daemon URL:** set `PARMEM_MCP_URL` (it passes through to child
  processes via parsidion's safe-env allowlist).

## Graph & Visualization

Once the vault is indexed, par-mem's built-in real-time 3D visualizer works
over it with **zero parsidion changes**:

```bash
par-mem ui        # prints/opens the daemon's UI endpoint
```

The visualizer renders the vault as a force-directed 3D knowledge graph —
notes as heading-section document nodes, `[[wikilinks]]` / `related:` /
`sources:` frontmatter and in-body markdown links as edges — with live
updates while a `par-mem watch` hold is active (parsidion's SessionStart
hook holds one automatically). It complements, rather than replaces,
parsidion's own Sigma.js visualizer (see [docs/VISUALIZER.md](VISUALIZER.md)).

**Parsidion's `graph.json` stays local.** The Sigma.js visualizer's
`graph.json` build (`build_graph.py`) keeps using `embeddings.db` for its
semantic-similarity edges — which independence preserves anyway — and
parsidion-derived wiki edges for structure.

**Shipped:** merging par-mem's in-body markdown-link edges — which
parsidion's `related:`-frontmatter-only wiki edges miss — into `graph.json`
as extra `kind: "wiki"` edges. See "Graph enrichment" below.

## Graph enrichment

`build_graph.py` optionally enriches its `kind: "wiki"` edges with par-mem's
in-body doc links. Frontmatter `related:` parsing (the always-on source)
only sees links declared in a note's frontmatter; par-mem's markdown
indexer additionally extracts `[[wikilinks]]` and markdown links written in
a note's body, which `build_graph.py` cannot see on its own.

On every run (unless `--no-parmem` is passed), `build_graph.py` calls
`resolve_parmem_backend()` — the same availability probe search uses (config
gate + binary on `PATH` + daemon `/health`; see "Troubleshooting" below) —
and, when it succeeds, runs:

```bash
par-mem doc-links --json --targets doc --limit 200000
```

against the vault. Each returned `source_path`/`target_path` pair that
resolves to two distinct, known note stems becomes an extra
`{"s", "t", "w": 1.0, "kind": "wiki"}` edge, deduplicated against the
frontmatter-derived wiki edges (and against itself). When one or more edges
were added, the output `graph.json`'s `meta` gains a `parmem_body_links`
count; when none were added — par-mem unavailable, no body links found, or
`--no-parmem` was passed — the key is omitted entirely (not written as
zero), and the `nodes`/`edges` content matches the pre-integration output.

- **Opt out:** `build_graph.py --no-parmem` skips the enrichment
  unconditionally.
- **Troubleshooting:** enrichment silently contributes zero edges whenever
  the standard availability probe fails — same probe as search; see
  "Troubleshooting" above for how to diagnose it.
- **Freshness:** enrichment reads par-mem's index as-is, not a live
  recompute — a body link written moments before a `graph.json` rebuild may
  not appear until the *next* rebuild, since the background reindex and the
  graph build are decoupled by design.

## Related Documentation

- [README.md](../README.md) — project overview and optional external tools
- [docs/EMBEDDINGS.md](EMBEDDINGS.md) — the local embeddings pipeline (the fallback)
- [docs/MCP.md](MCP.md) — parsidion-mcp server, including the `code_search` tool
- [docs/MCPL.md](MCPL.md) — the same optional-external-CLI integration pattern
