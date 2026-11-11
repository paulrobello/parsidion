"""SEC-108 tests — untrusted-content framing where content reaches the agent.

The codebase applies ``<content>`` delimiters + a SYSTEM untrusted-data
preamble on every *ingest* prompt (summarizer, classifier, doctor). The
one path that was missing it was ``additionalContext`` injection — the
path that reaches the primary agent with full authority. These tests pin
the framing on:

- ``session_start_hook._assemble_context`` — vault notes injected at
  session start are wrapped and labelled.
- ``session_start_hook`` ``--ai`` selector prompt — instructions precede
  the candidate note bodies (so an instruction inside a note cannot run
  before the model has read the task description).
- ``session_start_hook`` source=``compact`` snapshot restore — the
  post-compaction snapshot (moved here from post_compact_hook.py, whose
  stdout Claude Code never injects into context) is wrapped, and the
  previous "(Resume from where you left off above.)" comply-instruction
  stays removed.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import session_start_hook  # noqa: E402


class TestAssembleContextFraming:
    """``_assemble_context`` wraps body and delta in <content> with preamble."""

    def test_body_is_wrapped_in_content_delimiter(self) -> None:
        out = session_start_hook._assemble_context(
            header="## Vault context\n",
            body="### Note (foo.md)\nbody line\n",
            pending_notice="",
            delta_section="",
        )
        assert "<content>" in out
        assert "</content>" in out
        assert "### Note (foo.md)" in out
        # The untrusted-data preamble is present.
        assert "untrusted vault data" in out
        assert "SYSTEM:" in out

    def test_delta_section_is_grouped_inside_content(self) -> None:
        out = session_start_hook._assemble_context(
            header="## Vault context\n",
            body="### Note\n",
            pending_notice="",
            delta_section="### Since last time\n- new.md",
        )
        # The delta block appears between the opening <content> tag and the
        # regular body, so both are inside the untrusted framing.
        open_idx = out.index("<content>")
        close_idx = out.index("</content>")
        delta_idx = out.index("### Since last time")
        body_idx = out.index("### Note")
        assert open_idx < delta_idx < body_idx < close_idx

    def test_no_delta_still_wraps_body(self) -> None:
        out = session_start_hook._assemble_context(
            header="h",
            body="BODY",
            pending_notice="",
            delta_section="",
        )
        assert "<content>\nBODY\n</content>" in out


class TestPostCompactFraming:
    """SessionStart(source="compact") wraps the restored snapshot (SEC-108).

    The snapshot restore moved from post_compact_hook.py (whose PostCompact
    stdout Claude Code never injects into model context) to the SessionStart
    source="compact" path in session_start_hook.py. The framing contract
    travels with it: <content> delimiters + SYSTEM untrusted preamble, and
    the "(Resume from where you left off above.)" comply-instruction stays
    removed.
    """

    def _write_daily(self, tmp_path: Path) -> Path:
        daily = tmp_path / "daily.md"
        daily.write_text(
            "# Today\n\n"
            "## Pre-Compact Snapshot\n"
            "Current task: doing X.\n"
            "Files touched: a.py, b.py\n\n"
            "## Later Section\n"
            "Should not be in snapshot\n",
            encoding="utf-8",
        )
        return daily

    def test_snapshot_is_wrapped_and_resume_instruction_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daily = self._write_daily(tmp_path)

        # Stub the retrieval/config surfaces main() touches so the test
        # exercises only the compact-source snapshot-restore wiring.
        monkeypatch.setattr(
            session_start_hook,
            "build_session_context",
            lambda *args, **kwargs: ("BASE CONTEXT", 2),
        )
        monkeypatch.setattr(
            session_start_hook, "validate_config", lambda *args, **kwargs: []
        )
        monkeypatch.setattr(
            session_start_hook,
            "load_typed_config",
            lambda *args, **kwargs: types.SimpleNamespace(
                session_start_hook=types.SimpleNamespace(
                    ai_model=None, max_chars=4000, verbose_mode=False, debug=False
                )
            ),
        )
        monkeypatch.setattr(
            session_start_hook, "write_hook_event", lambda **kwargs: None
        )
        monkeypatch.setattr(
            session_start_hook,
            "existing_daily_path",
            lambda vault=None: daily,
        )

        # Flush stdin via monkeypatch so main() doesn't block, and pin argv
        # so argparse doesn't try to parse pytest's own command line.
        monkeypatch.setattr(sys, "argv", ["session_start_hook.py"])
        monkeypatch.setattr(
            "sys.stdin", _FakeStdin('{"cwd": "/tmp", "source": "compact"}')
        )

        import io

        buf = io.StringIO()
        monkeypatch.setattr("sys.stdout", buf)

        session_start_hook.main()

        parsed = json.loads(buf.getvalue())
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        # The snapshot is wrapped with <content> delimiters
        assert "<content>" in ctx
        assert "</content>" in ctx
        # The untrusted-data preamble is present
        assert "untrusted vault data" in ctx
        # The old comply-instruction has been removed.
        assert "Resume from where you left off" not in ctx
        # The framing labels the restored snapshot
        assert "Context restored from pre-compact snapshot" in ctx


class _FakeStdin:
    def __init__(self, s: str) -> None:
        self.s = s

    def read(self) -> str:
        return self.s


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
