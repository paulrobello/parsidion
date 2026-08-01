### Acme CLI argument parsing (Patterns/acme-cli-args.md)
Argparse subcommand dispatcher for the acme-cli tool; routes `acme-cli run` and
`acme-cli config` into dedicated handlers. Reuse this layout when adding new
subcommands. The dispatcher parses `--verbose` and `--config` flags centrally so
each subcommand does not re-implement arg handling.

### Baking sourdough (Knowledge/sourdough.md)
Long-fermentation sourdough technique. 75% hydration, 4-hour bulk ferment at
24C, cold retard overnight. A cooking hobby note; not related to software work.

### React hooks overview (Frameworks/react-hooks.md)
General reference for useEffect, useState, and useCallback in React component
design. Useful for frontend web work but not specific to the current project.

### Git rebase workflow (Tools/git-rebase.md)
How to rebase a feature branch onto main and resolve conflicts. Generic git
workflow reference applicable to any repository.
