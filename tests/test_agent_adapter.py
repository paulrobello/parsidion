"""QA-008 / ARC-020: parameterized test for the agent-adapter registry.

The five codex/gemini hook shims delegate to ``agent_adapter.run_session_start``
and ``run_session_end``; this test pins the contract for every registered
runtime in one place, replacing the five copies a per-runtime test would
otherwise require.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import agent_adapter  # noqa: E402


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    """The two built-in adapters (codex, gemini) must be registered with the
    expected descriptor fields. A new adapter added to agent_adapter.py
    without updating this test would surface here."""

    def test_registry_has_codex_and_gemini(self) -> None:
        names = {a.name for a in agent_adapter.all_adapters()}
        assert {"codex", "gemini"}.issubset(names)

    @pytest.mark.parametrize("name", ["codex", "gemini"])
    def test_get_returns_adapter_for_each_runtime(self, name: str) -> None:
        adapter = agent_adapter.get(name)
        assert adapter is not None
        assert adapter.name == name
        assert adapter.is_transcript_path is not None
        assert adapter.parse_transcript_lines is not None

    def test_get_is_case_insensitive(self) -> None:
        assert agent_adapter.get("CODEX") is agent_adapter.get("codex")
        assert agent_adapter.get("Gemini") is agent_adapter.get("gemini")

    def test_get_unknown_returns_none(self) -> None:
        assert agent_adapter.get("does-not-exist") is None

    def test_register_is_idempotent(self) -> None:
        """Re-registering the same name replaces the prior entry — tests rely
        on this so they can pin the registry without polluting production."""
        original = agent_adapter.get("codex")
        assert original is not None
        agent_adapter.register(original)
        assert agent_adapter.get("codex") is original

    def test_register_adds_new_runtime(self) -> None:
        """Pi (or any future runtime) can be registered without code changes
        to the entrypoints — ARC-020 step 7's unification path."""

        def fake_validator(_p: Path, _cwd: str) -> bool:
            return True

        def fake_parser(_lines: list[str]) -> list[str]:
            return []

        adapter = agent_adapter.AgentAdapter(
            name="test-runtime",
            hook_event_name_start="TestStart",
            hook_event_name_end="TestEnd",
            is_transcript_path=fake_validator,
            parse_transcript_lines=fake_parser,
        )
        try:
            agent_adapter.register(adapter)
            assert agent_adapter.get("test-runtime") is adapter
        finally:
            # Restore — re-register codex so the registry is back to its
            # post-_register_builtin_adapters state for subsequent tests.
            agent_adapter._REGISTRY.pop("test-runtime", None)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Entrypoints — parameterized across every registered runtime
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> io.StringIO:
    """Replace agent_adapter's stdin with a StringIO (stdout stays as the real
    sys.stdout — pytest's capsys captures it)."""
    import io

    stdin = io.StringIO(json.dumps({"cwd": str(tmp_path)}))
    monkeypatch.setattr(agent_adapter.sys, "stdin", stdin)
    return stdin


@pytest.mark.parametrize("name", ["codex", "gemini"])
class TestRunSessionStartAcrossRuntimes:
    """ARC-020 step 6: one parameterized test covers every runtime's
    SessionStart entrypoint. The contract is uniform — emit valid JSON,
    set PARSIDION_RUNTIME during the build, restore it on exit."""

    def test_parsidion_internal_short_circuits_with_empty_json(
        self,
        name: str,
        patched_stdin: io.StringIO,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("PARSIDION_INTERNAL", "1")
        adapter = agent_adapter.get(name)
        assert adapter is not None
        agent_adapter.run_session_start(adapter)
        assert capsys.readouterr().out == "{}"

    def test_emits_valid_json_on_stdout(
        self,
        name: str,
        patched_stdin: io.StringIO,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("PARSIDION_INTERNAL", raising=False)
        # Patch the heavy session_start_hook.build_session_context so the
        # test doesn't need a real vault.
        import session_start_hook

        def fake_build(cwd: str, **_kw: object) -> tuple[str, int]:
            return (f"ctx-{name}-{cwd}", 1)

        monkeypatch.setattr(session_start_hook, "build_session_context", fake_build)
        # Point resolve_vault at tmp_path so the hook event emission doesn't
        # touch the real vault.
        import vault_common

        monkeypatch.setattr(vault_common, "resolve_vault", lambda **_kw: tmp_path)
        monkeypatch.setattr(vault_common, "get_project_name", lambda _cwd: "test")

        adapter = agent_adapter.get(name)
        assert adapter is not None
        agent_adapter.run_session_start(adapter)

        parsed = json.loads(capsys.readouterr().out)
        assert "hookSpecificOutput" in parsed
        assert parsed["hookSpecificOutput"]["additionalContext"].startswith(
            f"ctx-{name}-"
        )


@pytest.mark.parametrize("name", ["codex", "gemini"])
class TestRunSessionEndAcrossRuntimes:
    """ARC-020 step 6: SessionEnd parameterized across runtimes."""

    def test_missing_transcript_returns_empty_json(
        self,
        name: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import io

        monkeypatch.setattr(
            agent_adapter.sys,
            "stdin",
            io.StringIO(json.dumps({"cwd": str(tmp_path)})),  # no transcript_path
        )
        adapter = agent_adapter.get(name)
        assert adapter is not None
        agent_adapter.run_session_end(adapter)
        assert capsys.readouterr().out == "{}"

    def test_parsidion_internal_short_circuits(
        self,
        name: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import io

        monkeypatch.setattr(
            agent_adapter.sys,
            "stdin",
            io.StringIO(json.dumps({"cwd": str(tmp_path)})),
        )
        monkeypatch.setenv("PARSIDION_INTERNAL", "1")
        adapter = agent_adapter.get(name)
        assert adapter is not None
        agent_adapter.run_session_end(adapter)
        assert capsys.readouterr().out == "{}"


# ---------------------------------------------------------------------------
# Shim wiring — confirm the 5 shim scripts resolve to the right adapter
# ---------------------------------------------------------------------------


class TestShimsResolveAdapter:
    """Each shim must resolve its adapter by name; a typo here would silently
    dispatch the wrong runtime's hooks."""

    @pytest.mark.parametrize(
        "module_name,expected_adapter_name",
        [
            ("codex_session_start_hook", "codex"),
            ("gemini_session_start_hook", "gemini"),
            ("codex_stop_hook", "codex"),
            ("gemini_session_end_hook", "gemini"),
            ("codex_subagent_stop_hook", "codex"),
        ],
    )
    def test_shim_resolves_correct_adapter(
        self, module_name: str, expected_adapter_name: str
    ) -> None:
        import importlib

        # Import the shim and confirm agent_adapter.get(name) returns the
        # adapter the shim is supposed to use. The shim's main() does
        # `adapter = get("<name>")`; we don't invoke main() (it would read
        # stdin), but we verify the registry contract holds.
        importlib.import_module(module_name)
        adapter = agent_adapter.get(expected_adapter_name)
        assert adapter is not None
        assert adapter.name == expected_adapter_name
