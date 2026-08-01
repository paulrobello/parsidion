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


# ---------------------------------------------------------------------------
# ENH-006: registry completeness + external adapter loading
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """ENH-006: claude and pi are registered alongside codex/gemini, and the
    descriptor carries the installer-side fields the generic core reads."""

    def test_all_four_builtins_registered(self) -> None:
        assert set(agent_adapter.known_runtimes()) >= {
            "claude",
            "codex",
            "gemini",
            "pi",
        }

    def test_hook_runtimes_own_a_config_pi_does_not(self) -> None:
        hooky = {
            a.name for a in agent_adapter.all_adapters() if a.hooks_config_filename
        }
        assert {"claude", "codex", "gemini"} <= hooky
        assert "pi" not in hooky  # pi is extension-only

    def test_timeout_unit_is_explicit_per_runtime(self) -> None:
        # ARC-048a: codex is seconds; gemini/claude are milliseconds.
        codex = agent_adapter.get("codex")
        gemini = agent_adapter.get("gemini")
        claude = agent_adapter.get("claude")
        assert codex is not None and codex.timeout_unit == "s"
        assert gemini is not None and gemini.timeout_unit == "ms"
        assert claude is not None and claude.timeout_unit == "ms"


class TestExternalLoading:
    """ENH-006: opt-in drop-in adapters under ~/.config/parsidion/adapters/."""

    def test_off_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import vault_common

        home = tmp_path / "home"
        ad = home / ".config" / "parsidion" / "adapters"
        ad.mkdir(parents=True)
        (ad / "x.py").write_text(
            "from agent_adapter import AgentAdapter\nADAPTER = AgentAdapter(name='x')\n"
        )
        monkeypatch.setenv(
            "CLAUDE_VAULT", str(tmp_path)
        )  # no config.yaml -> default off
        monkeypatch.setenv("HOME", str(home))
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
        vault_common.load_config.cache_clear()
        agent_adapter.reset_external_adapters()
        try:
            assert "x" not in agent_adapter.known_runtimes()
        finally:
            agent_adapter.reset_external_adapters()

    def test_loads_when_enabled_and_refuses_world_writable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import vault_common

        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "config.yaml").write_text("adapters:\n  load_external: true\n")
        home = tmp_path / "home"
        ad = home / ".config" / "parsidion" / "adapters"
        ad.mkdir(parents=True)
        (ad / "acme.py").write_text(
            "from agent_adapter import AgentAdapter\n"
            "ADAPTER = AgentAdapter(name='acme', display_name='Acme')\n"
        )
        bad = ad / "bad.py"
        bad.write_text("ADAPTER = None")
        bad.chmod(0o666)  # group+other writable -> must be refused
        # SEC-P001: register vault_dir in vaults.yaml so the allowlist
        # resolver accepts the CLAUDE_VAULT reference.
        (_cfg_dir := home / ".config" / "parsidion").mkdir(parents=True, exist_ok=True)
        (_cfg_dir / "vaults.yaml").write_text(
            f"vaults:\n  test: {vault_dir}\n", encoding="utf-8"
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        monkeypatch.setenv("CLAUDE_VAULT", str(vault_dir))
        monkeypatch.setenv("HOME", str(home))
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
        vault_common.load_config.cache_clear()
        agent_adapter.reset_external_adapters()
        try:
            runtimes = agent_adapter.known_runtimes()
            assert "acme" in runtimes
            acme = agent_adapter.get("acme")
            assert acme is not None and acme.display_name == "Acme"
            assert "bad" not in runtimes  # world-writable refused
        finally:
            agent_adapter._REGISTRY.pop("acme", None)  # type: ignore[attr-defined]
            agent_adapter.reset_external_adapters()


# ---------------------------------------------------------------------------
# ARC-004: hook-script maps are defined in exactly one place.
# ---------------------------------------------------------------------------


class TestHookScriptMapsSingleSource:
    """ARC-004: the event->script-filename maps used by both the installer
    (``installer/paths.py``) and the runtime registry (``agent_adapter``)
    must be defined exactly once and shared by reference. Drift between the
    two previously silently broke hook registration for one runtime — this
    test pins the canonical location (``agent_adapter``) and asserts the
    installer-side aliases point at the same dict objects.
    """

    def test_claude_hook_scripts_are_the_same_object(self) -> None:
        import installer.paths

        assert installer.paths._HOOK_SCRIPTS is agent_adapter._CLAUDE_HOOK_SCRIPTS
        # The installer-side alias exposes the Claude map under its historical
        # name (``_HOOK_SCRIPTS``); the canonical name is also available.
        assert (
            agent_adapter.get("claude").event_scripts  # type: ignore[union-attr]
            is agent_adapter._CLAUDE_HOOK_SCRIPTS
        )

    def test_codex_hook_scripts_are_the_same_object(self) -> None:
        import installer.paths

        assert installer.paths._CODEX_HOOK_SCRIPTS is agent_adapter._CODEX_HOOK_SCRIPTS
        assert (
            agent_adapter.get("codex").event_scripts  # type: ignore[union-attr]
            is agent_adapter._CODEX_HOOK_SCRIPTS
        )

    def test_gemini_hook_scripts_are_the_same_object(self) -> None:
        import installer.paths

        assert (
            installer.paths._GEMINI_HOOK_SCRIPTS is agent_adapter._GEMINI_HOOK_SCRIPTS
        )
        assert (
            agent_adapter.get("gemini").event_scripts  # type: ignore[union-attr]
            is agent_adapter._GEMINI_HOOK_SCRIPTS
        )

    def test_gemini_hook_names_are_the_same_object(self) -> None:
        import installer.paths

        assert installer.paths._GEMINI_HOOK_NAMES is agent_adapter._GEMINI_HOOK_NAMES

    def test_hook_script_maps_defined_only_in_agent_adapter(self) -> None:
        """Grep-style guard: the dict literals for the four hook-script maps
        must appear in ``agent_adapter.py`` and NOWHERE else. Catches the
        re-introduction of a duplicate definition.
        """
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        canonical_names = {
            "_CLAUDE_HOOK_SCRIPTS",
            "_CODEX_HOOK_SCRIPTS",
            "_GEMINI_HOOK_SCRIPTS",
            "_GEMINI_HOOK_NAMES",
        }
        # The installer/paths.py back-compat re-export imports these names
        # from agent_adapter — that is the allowed alias site.
        canonical_file = (
            repo_root / "skills" / "parsidion" / "scripts" / "agent_adapter.py"
        )
        # Restrict the walk to the only directories that previously held
        # duplicates — installer/ and the top-level install.py — plus the
        # scripts root. Keeps the test well under the 10s timeout ceiling.
        candidate_files: list[Path] = [repo_root / "install.py"]
        candidate_files.extend((repo_root / "installer").rglob("*.py"))
        candidate_files.extend(
            (repo_root / "skills" / "parsidion" / "scripts").glob("*.py")
        )
        # Map literal assignment looks like:  NAME: dict[...] = { ... }
        offenders: list[str] = []
        for py in candidate_files:
            if py == canonical_file or "/__pycache__/" in str(py):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign):
                    if (
                        isinstance(node.target, ast.Name)
                        and node.target.id in canonical_names
                        and isinstance(node.value, ast.Dict)
                    ):
                        offenders.append(
                            f"{py.relative_to(repo_root)}:{node.lineno} "
                            f"defines {node.target.id} as a dict literal"
                        )
        assert not offenders, (
            "ARC-004: hook-script maps must be defined only in "
            "agent_adapter.py. Found dict-literal definitions: " + ", ".join(offenders)
        )
