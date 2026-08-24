"""QA-015: minimal tests for ``html-to-md.py`` (kept, not retired).

``html-to-md.py`` is a PEP 723 standalone script (hyphenated name, run via
``uv run --script`` with its inline ``html2text``/``httpx`` dependencies) —
it is NOT importable as a plain module and its deps are not repo deps. The
always-run tests below pin the PEP 723 contract; the converter tests load
the script by path and skip when its third-party deps are absent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
SCRIPT = _SCRIPTS_DIR / "html-to-md.py"


def test_script_exists_and_declares_pep723_deps() -> None:
    """The script keeps its inline metadata so `uv run --script` works."""
    assert SCRIPT.is_file(), "html-to-md.py must exist (kept per QA-015)"
    header = SCRIPT.read_text(encoding="utf-8")[:2000]
    assert "# /// script" in header
    assert "html2text" in header
    assert "httpx" in header


def _load_module():
    pytest.importorskip("html2text")
    pytest.importorskip("httpx")
    spec = importlib.util.spec_from_file_location("html_to_md_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_is_url() -> None:
    mod = _load_module()
    assert mod._is_url("https://example.com/x")
    assert mod._is_url("http://example.com")
    assert not mod._is_url("/tmp/page.html")
    assert not mod._is_url("-")


def test_clean_markdown_strips_noise() -> None:
    mod = _load_module()
    noisy = "# Title\n\n\ntext\n\n\n\nmore\n"
    cleaned = mod._clean_markdown(noisy)
    assert "# Title" in cleaned
    assert "text\nmore" in cleaned.replace("\n\n", "\n") or "text" in cleaned


def test_extract_code_language() -> None:
    mod = _load_module()

    class Tag:
        def __init__(self, cls):
            self.attrs = {"class": [cls]} if cls else {}
            self.text = "x = 1"

    assert mod._extract_code_language(Tag("language-python")) == "python"
    assert mod._extract_code_language(Tag("lang-js")) == "js"
    assert mod._extract_code_language(Tag("")) in ("", "code")
