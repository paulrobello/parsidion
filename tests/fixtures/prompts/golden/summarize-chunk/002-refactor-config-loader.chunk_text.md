User: the config loader is a mess. three places parse YAML by hand, each with
slightly different defaults. can you unify it?

Agent: Mapped the three call sites: server.go, worker.go, and migrate.go. Each
calls yaml.Unmarshal into a local struct and then layers its own defaults on
top. The divergence is in the defaults, not the parse — they all read the same
config.yaml file.

User: ok so what is the fix?

Agent: Extract a single config.Load(path) that parses YAML once into one Config
struct, applies the canonical defaults in applyDefaults, and returns an
immutable value. The three callers become one-liners. The loader also
validates required keys (port, database_url) and returns a typed error so a
missing config does not become a silent zero-value at runtime.

User: does this break the existing yaml files?

Agent: No. Field tags are unchanged, so every existing config.yaml still loads.
Added a fixture test that loads the three sample configs from testdata/ and
asserts the resolved values match the old per-caller defaults exactly — that is
the regression guard.

User: run the tests.

Agent: green. config.Load now has full coverage on the happy path and the
missing-key error branch. The three hand-rolled parsers are deleted.
