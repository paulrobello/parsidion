# Session transcript — CLI argument design for a new tool

User: Design the CLI for vault-stats. What flags?

Assistant: vault-stats is an analytics tool over the vault. Subcommands and flags:

  vault-stats --summary          # counts, growth, top tags (default)
  vault-stats --stale            # notes with no incoming links >30d
  vault-stats --top-linked       # most-referenced notes
  vault-stats --by-project       # counts per project
  vault-stats --growth           # notes added per week
  vault-stats --tags             # tag frequency cloud
  vault-stats --hooks N          # last N hook events
  vault-stats --dashboard        # combined dashboard

Design rule: each flag is a self-contained "view" that prints one table and exits. No chaining. This makes the output scriptable (one flag = one parseable blob) and avoids the combinatorial complexity of flag interaction.

Use argparse subparsers vs. mutually-exclusive flags? Argparse flags with a single dash (-summary, -stale) keep invocation short; the tool is interactive-first, so brevity wins over formality. Reserve subparsers for when a flag needs its own arguments (e.g. -hooks N).

Output format: default JSON (machine-readable), -text for human, -rich for colored. Three formats, same data — matches vault-search's convention.
