"""SEC-026: the cron line must shell-quote paths, not merely double-quote them.

Double quotes leave ``$`` and backticks live in the cron shell: a HOME
containing ``$(...)`` or a ``"`` would word-split or command-substitute.
``shlex.quote`` emits single-quoted strings when the path needs them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer import schedule  # noqa: E402


class TestCronLineQuoting:
    def _cron_line(
        self, capsys: pytest.CaptureFixture[str], script: Path, uv: str
    ) -> str:
        schedule._schedule_summarizer_cron(script, uv, dry_run=True, hour=3)
        out = capsys.readouterr().out
        for line in out.splitlines():
            if "0 3 * * *" in line:
                return line.strip()
        raise AssertionError(f"cron line not found in output:\n{out}")

    def test_plain_paths_remain_readable(self, tmp_path: Path, capsys) -> None:
        line = self._cron_line(
            capsys, tmp_path / "summarize_sessions.py", "/usr/local/bin/uv"
        )
        assert line.startswith("0 3 * * * /usr/local/bin/uv run --no-project")

    def test_space_in_path_is_single_quoted(self, tmp_path: Path, capsys) -> None:
        script = tmp_path / "My Scripts" / "summarize_sessions.py"
        line = self._cron_line(capsys, script, "/usr/local/bin/uv")
        # shlex.quote emits one single-quoted string around the whole path.
        assert f"'{script}'" in line

    def test_dollar_and_quote_in_path_not_interpolated(
        self, tmp_path: Path, capsys
    ) -> None:
        """A path with shell metacharacters must come through shlex.quote."""
        evil = tmp_path / 'ev"$HOME"il'
        line = self._cron_line(capsys, evil, "/usr/local/bin/uv")
        # shlex.quote single-quotes the whole path, so the inner double
        # quotes and $ are inert under the cron shell.
        assert f"'{evil}'" in line
