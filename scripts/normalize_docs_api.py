#!/usr/bin/env python3
"""Canonicalize generated docs/api artifacts that are byte-unstable across platforms.

The DOC-003 drift gate (make docs-api-check) regenerates docs/api and diffs
against the committed snapshot, so every emitted byte must be identical on
every machine. Two generators produce content-identical but byte-different
output depending on platform:

- pdoc's prebuilt search index (docs/api/python/search.js) serializes lunr
  posting maps in insertion order, which follows the document processing
  order -- not identical across platforms. The JSON object is re-dumped with
  sorted keys and fixed separators; consumers parse the JSON, so member
  order is not functional.

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

BLOB_RE = re.compile(r'=\s*"([A-Za-z0-9+/=]{40,})"')
SEARCHJS_RE = re.compile(r"(const docs = )(\{.*\})(;)", re.S)


def normalize_search_js(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    m = SEARCHJS_RE.search(text)
    if not m:
        return False
    try:
        obj = json.loads(m.group(2))
    except json.JSONDecodeError:
        return False
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
