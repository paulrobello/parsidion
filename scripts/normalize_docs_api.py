#!/usr/bin/env python3
"""Canonicalize generated docs/api artifacts that are byte-unstable across platforms.

The DOC-003 drift gate (make docs-api-check) regenerates docs/api and diffs
against the committed snapshot, so every emitted byte must be identical on
every machine. Three generators produce content-identical but byte-different
output depending on platform:

- pdoc's prebuilt search index (docs/api/python/search.js) serializes lunr
  posting maps in insertion order, which follows the document processing
  order -- not identical across platforms. The JSON object is re-dumped with
  sorted keys and fixed separators; consumers parse the JSON, so member
  order is not functional.

- The same index's inverted tries contain tokens derived from the machine
  paths that survived into default-value reprs before the text scrub ran.
  The tokens themselves ("tmp/parsidion" on Linux, "private/tmp/parsidion"
  on macOS, where realpath(3) resolves /tmp -> /private/tmp) cannot be
  rewritten by the scrub, because the trie stores each token as one
  character per nesting level. Those machine-path tokens are DROPPED from
  the tries (with their postings); the only searchable content lost is the
  path text of a handful of path-derived constants.

- typedoc's assets (navigation.js, hierarchy.js, search.js under
  docs/api/visualizer/assets/) embed base64 zlib-compressed JSON. The
  decompressed payload is identical everywhere, but the deflate stream bytes
  depend on the zlib build that produced them. The blob is re-stored with
  zlib level 0 (stored blocks): no entropy coding, byte-identical on every
  zlib implementation. Costs some file size; buys provable determinism.

ARC-104 additionally moved the machine-path/display scrub here out of the
Makefile recipe, where it lived as a chained ``perl -pi -e`` one-liner. It runs
FIRST (before the two canonicalizations above), because the trie-drop logic
depends on seeing which machine-path tokens survived the text scrub. Condensed
trap notes for the scrub, each of which produced a real incident or is load
bearing for byte-exactness:

- ``perl -pi -e`` is a per-RECORD filter with ``$/ = "\\n"``, so ``$_`` is one
  line INCLUDING its newline. Character classes match a newline, so a whole-file
  ``re.sub`` would match brace groups and frozenset displays spanning pdoc's
  multiline signature layout that perl never matched. The scrub therefore
  splits on ``\\n`` and applies the rule chain per line, newline attached.
- Path needles are applied in a fixed order: the realpath-resolved generator
  root BEFORE the literal one. macOS resolves ``/tmp`` -> ``/private/tmp``, so
  rewriting the literal first leaves ``/private<repo-root>`` behind -- the
  recorded incident this ordering exists to prevent.
- An EMPTY needle must be a hard error, not a silent skip: an unset variable
  that never reached perl degraded to the literal-path rule and produced that
  same ``/private`` residue with no diagnostic.
- The scrub operates on bytes. Every needle and replacement is ASCII, and
  byte-exactness is the acceptance bar (``make docs-api-check`` diffs the
  committed tree), so no decode/encode or newline translation is involved.
- ``sorted()`` over bytes is codepoint order, matching perl's default ``sort``;
  elements split on ``,\\s+`` and rejoin with exactly ``", "``.

Usage:
  normalize_docs_api.py <docs-api-out-dir>
      [--repo-root NEEDLE ...] [--home NEEDLE ...]
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import zlib
from pathlib import Path
from typing import Any

BLOB_RE = re.compile(r'=\s*"([A-Za-z0-9+/=]{40,})"')
SEARCHJS_RE = re.compile(r"(const docs = )(\{.*\}|\[.*\])(;)", re.S)

# Token prefixes dropped from every search-index trie. docs-api-gen imports
# its subjects from /tmp/parsidion-docs-gen and /tmp/parsidion-docs-home, and
# macOS realpath(3) resolves the /tmp ancestor to /private/tmp, so the same
# value yields "private/tmp/parsidion" there and "tmp/parsidion" elsewhere.
# Neither token is legitimate content; dropping both keeps the tries
# identical on every platform.
DROPPED_TOKEN_PREFIXES = ("private/", "tmp/parsidion")

# --- ARC-104: the machine-path / display scrub (ported from the Makefile) ----

# Files the scrub visits, matching the recipe's
# ``find ... \( -name '*.html' -o -name '*.js' \)``.
SCRUB_SUFFIXES = (".html", ".js")

REPO_ROOT_TOKEN = b"<repo-root>"
HOME_TOKEN = b"<home>"

# pdoc's cosmetic "view value" toggle: an input carrying the toggle state and
# the label that flips it. Both are pure markup, and whether pdoc emits them at
# all depends on PRE-scrub rendered lengths, so they cannot be allowed to vary.
_TOGGLE_INPUT_RE = re.compile(
    rb'<input id="[^"]*view-value" class="view-value-toggle-state"[^>]*>\s*'
)
_TOGGLE_LABEL_RE = re.compile(
    rb'<label class="view-value-button pdoc-button" for="[^"]*"></label>'
)
# lunr stores default_value FIELD LENGTHS (token counts) computed from the
# pre-scrub strings; pin them so a machine path's token count cannot leak.
_DEFAULT_VALUE_LEN_RE = re.compile(rb'"default_value": \d+')

# set/frozenset default-value reprs iterate in hash order, which is not stable
# across interpreter builds even under PYTHONHASHSEED=0. Sort the elements of
# every frozenset display and of every colon-free brace group made purely of
# quoted elements (a dict display contains colons and keeps insertion order).
_FROZENSET_RE = re.compile(rb"\bfrozenset\(\{([^{}]+)\}\)")
_ELEMENT_SPLIT_RE = re.compile(rb",\s+")
# The three ways pdoc can render a quote in a default-value display.
_OPEN_QUOTE_RE = re.compile(rb"'|&#39;|&#x27;")


def _sorted_elements(inner: bytes) -> bytes:
    """Return *inner* split on ``,\\s+``, sorted, rejoined with ``", "``."""
    return b", ".join(sorted(_ELEMENT_SPLIT_RE.split(inner)))


def _sort_frozenset(match: re.Match[bytes]) -> bytes:
    return b"frozenset({" + _sorted_elements(match.group(1)) + b"})"


def _is_quoted_element_list(inner: bytes) -> bool:
    """Whether *inner* is what perl's brace-group pattern accepted between braces.

    The original pattern was
    ``\\{(Q[^{}()]*?Q(?:,\\s*Q[^{}()]*?Q)*)\\}`` with ``Q`` one of ``'``,
    ``&#39;``, ``&#x27;``. Translated literally it is catastrophically slow in
    Python's ``re`` (measured: perl 0.24 s vs >90 s on the 684 KB minified line
    in ``docs/api/python/search.js``), so the acceptance test is a differential
    against perl rather than a transliteration.

    The predicate collapses because the repeated group is optional: any string
    the k-element form accepts, the ONE-element form ``Q B Q`` also accepts --
    ``B`` is ``[^{}()]*?``, which already permits the commas, spaces and quotes
    that separate elements. So a match exists exactly when *inner* opens with a
    quote token, ends with a quote token, the two do not overlap, and *inner*
    contains none of ``{``, ``(``, ``)`` (``}`` is impossible: the caller cuts
    at the first one). The three spellings have mutually exclusive suffixes, so
    both tokens are identified in O(1).
    """
    if b"{" in inner or b"(" in inner or b")" in inner:
        return False
    for token in (b"'", b"&#39;", b"&#x27;"):
        if inner.endswith(token):
            closing_start = len(inner) - len(token)
            break
    else:
        return False
    opening = _OPEN_QUOTE_RE.match(inner)
    # The opening token's span must not reach into the closing quote.
    return opening is not None and opening.end() <= closing_start


def _sort_brace_groups(line: bytes) -> bytes:
    """Sort the elements of every colon-free quoted brace group in *line*.

    A dict display contains a colon and keeps its insertion order. Because the
    group body admits no braces, a match always runs from a ``{`` to the FIRST
    ``}`` after it, which is what makes the left-to-right scan below equivalent
    to the original global substitution -- including the failure behaviour of
    advancing one byte past the ``{`` and retrying.
    """
    out: list[bytes] = []
    pos = 0
    while True:
        open_at = line.find(b"{", pos)
        if open_at < 0:
            break
        close_at = line.find(b"}", open_at + 1)
        if close_at < 0:
            break
        inner = line[open_at + 1 : close_at]
        if _is_quoted_element_list(inner):
            body = inner if b":" in inner else _sorted_elements(inner)
            out.append(line[pos:open_at])
            out.append(b"{" + body + b"}")
            pos = close_at + 1
        else:
            out.append(line[pos : open_at + 1])
            pos = open_at + 1
    out.append(line[pos:])
    return b"".join(out)


Rule = Any  # Callable[[bytes], bytes]


def _regex_rule(pattern: re.Pattern[bytes], replacement: Any) -> Rule:
    """Adapt a compiled pattern + replacement into the callable rule shape."""

    def apply(line: bytes) -> bytes:
        return pattern.sub(replacement, line)

    return apply


def build_scrub_rules(repo_roots: list[str], homes: list[str]) -> list[Rule]:
    """Assemble the ordered scrub rule chain.

    Args:
        repo_roots: Path needles rewritten to ``<repo-root>``, most-resolved
            first (see the module docstring's ordering trap).
        homes: Path needles rewritten to ``<home>``, same ordering rule.

    Returns:
        ``(compiled pattern, replacement)`` pairs to apply in order.

    Raises:
        ValueError: if any needle is empty.
    """
    for needle in (*repo_roots, *homes):
        if not needle:
            raise ValueError(
                "empty path needle: a needle that never reaches the scrub "
                "degrades to the literal-path rule and leaves a machine-path "
                "prefix in the output"
            )
    rules: list[Rule] = []
    for needle in repo_roots:
        rules.append(
            _regex_rule(re.compile(re.escape(needle.encode())), REPO_ROOT_TOKEN)
        )
    for needle in homes:
        rules.append(_regex_rule(re.compile(re.escape(needle.encode())), HOME_TOKEN))
    # typedoc "Defined in" URLs for base types resolved through node_modules
    # (e.g. Error from the TS lib) embed the installer's node_modules layout:
    # a hoisted bun install emits lib/../node_modules/typescript/lib/... while
    # an isolated store emits node_modules/.bun/typescript@X/node_modules/....
    # Collapse any hop through node_modules onto one canonical URL so the
    # committed snapshot is layout-independent. Line anchors into node_modules
    # d.ts files are dropped too: bun-types is injected by the bun runtime
    # (not the lockfile), so its line numbers float with the runner's bun
    # version -- and the links are dead anyway (the files are not in the repo).
    rules.append(
        _regex_rule(
            re.compile(rb'(blob/main/)[^"\'<>#\s]*node_modules/'),
            rb"\1node_modules/",
        )
    )
    rules.append(
        _regex_rule(
            re.compile(rb"(node_modules/[^\"'\s<>#]+?)(#L\d+|:\d+)(?=[\"'<\s])"),
            rb"\1",
        )
    )
    rules.append(_regex_rule(_TOGGLE_INPUT_RE, b""))
    rules.append(_regex_rule(_TOGGLE_LABEL_RE, b""))
    rules.append(_regex_rule(_DEFAULT_VALUE_LEN_RE, b'"default_value": 1'))
    rules.append(_regex_rule(_FROZENSET_RE, _sort_frozenset))
    rules.append(_sort_brace_groups)
    return rules


def scrub_line(line: bytes, rules: list[Rule]) -> bytes:
    """Apply the rule chain to one record (newline attached, as perl sees it)."""
    for rule in rules:
        line = rule(line)
    return line


def scrub_bytes(data: bytes, rules: list[Rule]) -> bytes:
    """Apply the rule chain per ``\\n``-terminated record, like ``perl -p``.

    ``bytes.splitlines()`` is deliberately NOT used: it also splits on ``\\r``,
    ``\\v``, ``\\f`` and the information separators, which perl's ``$/ = "\\n"``
    record reader does not.
    """
    records = data.split(b"\n")
    trailing = records.pop()  # text after the final newline ("" when data ends in one)
    out = [scrub_line(record + b"\n", rules) for record in records]
    if trailing:
        out.append(scrub_line(trailing, rules))
    return b"".join(out)


def scrub_file(path: Path, rules: list[Rule]) -> bool:
    """Scrub one file in place. Returns True when the bytes changed."""
    data = path.read_bytes()
    new = scrub_bytes(data, rules)
    if new == data:
        return False
    path.write_bytes(new)
    return True


def scrub_tree(root: Path, rules: list[Rule]) -> int:
    """Scrub every ``*.html`` / ``*.js`` file under *root*. Returns the count changed."""
    changed = 0
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SCRUB_SUFFIXES and scrub_file(path, rules):
            changed += 1
    return changed


def _extract(node: dict[str, Any], prefix: str, out: dict[str, dict[str, Any]]) -> None:
    docs = node.get("docs") or {}
    if docs:
        out[prefix] = docs
    for key, child in node.items():
        if key in ("docs", "df"):
            continue
        _extract(child, prefix + key, out)


def _rebuild(tokens: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {"docs": {}, "df": 0}
    for token in sorted(tokens):
        node = root
        for ch in token:
            node = node.setdefault(ch, {"docs": {}, "df": 0})
        node["docs"] = tokens[token]
        node["df"] = len(tokens[token])
    return root


def _normalize_tries(obj: dict[str, Any]) -> None:
    index = obj.get("index") or {}
    for wrapper in index.values():
        root = wrapper.get("root") if isinstance(wrapper, dict) else None
        if not isinstance(root, dict):
            continue
        tokens: dict[str, dict[str, Any]] = {}
        _extract(root, "", tokens)
        kept = {
            t: d for t, d in tokens.items() if not t.startswith(DROPPED_TOKEN_PREFIXES)
        }
        if kept != tokens:
            wrapper["root"] = _rebuild(kept)


def normalize_search_js(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    m = SEARCHJS_RE.search(text)
    if not m:
        return False
    try:
        obj = json.loads(m.group(2))
    except json.JSONDecodeError:
        return False
    if isinstance(obj, dict):
        _normalize_tries(obj)
        documents = obj.get("documentStore", {}).get("docs", {})
        obj = list(documents.values()) if isinstance(documents, dict) else []
    if not isinstance(obj, list):
        return False
    obj.sort(
        key=lambda document: (
            str(document.get("fullname", "")),
            str(document.get("kind", "")),
            str(document.get("qualname", "")),
        )
    )
    canonical = json.dumps(obj, sort_keys=True, separators=(", ", ": "))
    path.write_text(text[: m.start(2)] + canonical + text[m.end(2) :], encoding="utf-8")
    return True


def normalize_asset_blobs(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    def store(match: re.Match[str]) -> str:
        nonlocal changed
        raw = zlib.decompress(base64.b64decode(match.group(1)))
        compressor = zlib.compressobj(0, zlib.DEFLATED, 15)
        blob = compressor.compress(raw) + compressor.flush()
        out = base64.b64encode(blob).decode("ascii")
        if out != match.group(1):
            changed = True
        return '= "' + out + '"'

    new = BLOB_RE.sub(store, text)
    if changed:
        path.write_text(new, encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="normalize_docs_api.py",
        description="Canonicalize generated docs/api artifacts (ARC-104 / DOC-003).",
    )
    parser.add_argument("out_dir", help="Generated docs/api output directory.")
    parser.add_argument(
        "--repo-root",
        action="append",
        default=[],
        metavar="NEEDLE",
        help=(
            "Path needle rewritten to <repo-root>. Repeatable; pass the "
            "realpath-resolved form BEFORE the literal one."
        ),
    )
    parser.add_argument(
        "--home",
        action="append",
        default=[],
        metavar="NEEDLE",
        help="Path needle rewritten to <home>. Repeatable, same ordering rule.",
    )
    args = parser.parse_args(argv[1:])
    root = Path(args.out_dir)

    if args.repo_root or args.home:
        try:
            rules = build_scrub_rules(args.repo_root, args.home)
        except ValueError as exc:
            print(f"normalize_docs_api: {exc}", file=sys.stderr)
            return 2
        # The scrub must precede both canonicalizations below: the trie-drop
        # logic keys off which machine-path tokens survived it.
        scrub_tree(root, rules)

    search_js = root / "python" / "search.js"
    if search_js.exists():
        normalize_search_js(search_js)

    for name in ("navigation.js", "hierarchy.js", "search.js"):
        asset = root / "visualizer" / "assets" / name
        if asset.exists():
            normalize_asset_blobs(asset)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
