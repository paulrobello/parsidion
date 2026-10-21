## Graph Color Groups

The Obsidian graph at `<resolved vault>/.obsidian/graph.json` uses color groups to visually categorize nodes by tag.

### Current Color Groups

| Group | Tags | RGB (decimal) |
|---|---|---|
| Projects | `#synknot`, `#parvitar`, `#parsistant`, `#termflix`, `#parvault`, `#parsidion`, `#pkm`, `#vault`, `#par-cc-bot`, `#parllama`, `#par-dc-bot`, `#par-particle-life`, `#par-shape-2d`, `#par_qr_3d`, `#par_scrape`, `#par-gpt`, `#par-ocr`, `#par_infini_sweeper`, `#parsplat`, `#pim-leaderboard`, `#par-ai-core`, `#cctmux` | 48340 |
| Debugging | `#debugging` | 16733986 |
| Patterns | `#memory`, `#migration`, `#sync`, `#architecture`, `#pattern`, `#automation`, `#config`, `#event-driven`, `#pipeline`, `#subprocess`, `#crdt`, `#layered-config`, `#rate-limiting`, `#semantic-search`, `#vector-search`, `#codesign`, `#state-machine`, `#concurrency` | 5025616 |
| Research | `#research`, `#qdrant`, `#embeddings`, `#vector-database`, `#ocr` | 10233776 |
| Tools & SDKs | `#claude-code`, `#claude-sdk`, `#claude`, `#rich`, `#mcp`, `#ollama`, `#maturin`, `#redis`, `#websockets`, `#sentry`, `#mermaid`, `#mermaid-cli`, `#custom-tools`, `#acp-protocol`, `#tool`, `#api`, `#encryption`, `#discord`, `#security`, `#git`, `#performance`, `#plugin`, `#cli`, `#llm`, `#hooks`, `#sqlite`, `#agent`, `#pydantic`, `#langchain`, `#multi-provider`, `#docker`, `#xdg`, `#image`, `#textual`, `#typer`, `#fastapi`, `#websocket`, `#image-processing`, `#json`, `#yaml`, `#ai` | 2201331 |
| Languages | `#rust`, `#python`, `#swift`, `#swiftui`, `#typescript`, `#nextjs`, `#react`, `#macos`, `#macos-26`, `#rust-packages`, `#ui`, `#ux`, `#documentation`, `#sql`, `#ios`, `#python-bindings` | 16761095 |
| Terminal | `#terminal`, `#tmux`, `#par-term`, `#par-term-emu-core-rust`, `#tui`, `#vt100`, `#vte`, `#pty`, `#ansi`, `#crossterm`, `#svt` | 38536 |
| Graphics / 3D | `#wgpu`, `#sdf`, `#sdf-terrain`, `#voxel`, `#fractals`, `#mandel`, `#vrm`, `#avatar`, `#face-tracking`, `#arkit`, `#graphics`, `#fractal`, `#gpu-rendering`, `#animation`, `#gaussian-splatting`, `#physics`, `#rendering`, `#simulation`, `#glsl`, `#shader`, `#shaders`, `#3d-printing`, `#par-fractal`, `#voxel-world`, `#sparse-voxel-tree`, `#sub-voxel`, `#wgsl`, `#vulkan`, `#game` | 15277667 |
| Daily | `#daily` | 16744448 |

### When to Update

Update `graph.json` when:
- A new project tag is introduced (add to the Projects group)
- A new language or framework tag appears frequently (add to Languages or Tools/AI)
- A new topic cluster emerges that warrants its own color group
- An existing tag is renamed or removed

### How to Update

1. Read `<resolved vault>/.obsidian/graph.json`
2. Locate the matching `colorGroups` entry by its query pattern
3. To **add a tag to an existing group**: append `OR tag:#newtag` to the query string
4. To **create a new group**: append a new object to the `colorGroups` array:
   ```json
   {
     "query": "tag:#newtag",
     "color": { "a": 1, "rgb": <decimal_rgb> }
   }
   ```
5. Write the updated file — Obsidian picks up changes automatically

### RGB Format

Colors are stored as a single decimal integer (e.g., `16733986` = `#FF2DA2` in hex). To convert:
- Hex to decimal: `int("FF2DA2", 16)`
- Choose colors that are visually distinct from existing groups

### Color Group Priority

Groups are evaluated in order — the **first matching group wins**. Place more specific queries (e.g., a single project tag) before broad ones (e.g., `#research`).

### Auditing Coverage

Use `check_graph_coverage.py` to find vault tags that are not covered by any color group and to spot stale group entries (tags in `graph.json` that no longer exist in the vault):

```bash
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py

# Only report uncovered tags used 2+ times
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --threshold 2

# Machine-readable JSON output
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --json
```

### Full Graph Colorizer Workflow

Run this workflow after batch summarizations, vault doctor runs, or whenever the tag taxonomy changes significantly:

1. **Merge duplicate tags first** — run `vault_doctor.py --fix-tags --execute` to merge plural/singular, hyphen/underscore, and collapsed duplicates. This reduces tag sprawl before colorizing.
2. **Rebuild the index** — run `update_index.py` so the tag cloud reflects the current state.
3. **Audit coverage** — run `check_graph_coverage.py --threshold 2` to see which tags are uncovered and what groups they should belong to.
4. **Update `graph.json`** — read `<resolved vault>/.obsidian/graph.json`, add uncovered tags to the appropriate `colorGroups` entry by appending `OR tag:#newtag` to the query string.
5. **Verify** — re-run the audit to confirm coverage is ≥ 90%. Obsidian picks up `graph.json` changes automatically.

The `--json` output from `check_graph_coverage.py` includes a `stats` object with `total_vault_tags`, `covered`, `uncovered`, and `stale` counts for scripting.
