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

Usage: normalize_docs_api.py <docs-api-out-dir>
"""

from __future__ import annotations

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
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[1])

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
