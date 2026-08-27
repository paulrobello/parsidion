from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import ai_backend  # noqa: E402 — ARC-006: patch internals where they live
import vault_config  # noqa: E402


_RUNTIME_ENV_KEYS = (
    "PARSIDION_RUNTIME",
    "CODEX_SANDBOX",
    "CODEX_SESSION_ID",
    "CODEX_HOME",
    "CLAUDECODE",
)


def _reset_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_text: str = ""
) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(config_text, encoding="utf-8")
    for key in _RUNTIME_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    vault_config._clear_config_cache()
    return tmp_path


class TestConfigSchema:
    def test_schema_accepts_ai_backend_and_codex_cli_sections(self) -> None:
        assert vault_config._CONFIG_SCHEMA["ai"]["backend"] == (str,)
        assert vault_config._CONFIG_SCHEMA["ai_models"]["claude"] == (dict,)
        assert vault_config._CONFIG_SCHEMA["ai_models"]["codex"] == (dict,)
        assert vault_config._CONFIG_SCHEMA["codex_cli"]["command"] == (str,)
        assert vault_config._CONFIG_SCHEMA["codex_cli"]["timeout"] == (int, float)
        assert vault_config._CONFIG_SCHEMA["codex_cli"]["sandbox"] == (
            str,
            type(None),
        )
        assert vault_config._CONFIG_SCHEMA["codex_cli"]["ephemeral"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["codex_cli"]["skip_git_repo_check"] == (
            bool,
        )
        assert vault_config._CONFIG_SCHEMA["ai_models"]["grok"] == (dict,)
        assert vault_config._CONFIG_SCHEMA["grok_cli"]["command"] == (str,)
        assert vault_config._CONFIG_SCHEMA["grok_cli"]["timeout"] == (int, float)
        assert vault_config._CONFIG_SCHEMA["grok_cli"]["minimal_context"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["grok_cli"]["system_prompt"] == (str,)
        # SEC-202: grok_cli.allow_tools registers as a bool (double opt-in).
        assert vault_config._CONFIG_SCHEMA["grok_cli"]["allow_tools"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["claude_cli"]["minimal_context"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["claude_cli"]["system_prompt"] == (str,)
        assert vault_config._CONFIG_SCHEMA["claude_cli"]["timeout"] == (int, float)


class TestResolveAiBackend:
    def test_auto_uses_codex_runtime_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "codex")
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_auto_uses_claude_runtime_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "claude")
        monkeypatch.setenv("CODEX_SANDBOX", "read-only")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    @pytest.mark.parametrize("codex_key", ["CODEX_SANDBOX", "CODEX_SESSION_ID"])
    def test_auto_uses_codex_environment_hints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex_key: str
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv(codex_key, "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_auto_uses_claudecode_when_no_codex_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_auto_prefers_codex_runtime_hint_over_claudecode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("CODEX_SANDBOX", "read-only")
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_auto_defaults_to_claude_when_no_strong_hints(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_explicit_codex_backend_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_none_backend_disables_ai(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: none\n")

        assert ai_backend.resolve_ai_backend(vault=vault) == "none"

    def test_codex_home_alone_does_not_select_codex(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_auto_uses_grok_runtime_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "grok")

        assert ai_backend.resolve_ai_backend(vault=vault) == "grok-cli"

    def test_explicit_grok_backend_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: grok-cli\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "claude")

        assert ai_backend.resolve_ai_backend(vault=vault) == "grok-cli"

    def test_invalid_backend_falls_back_to_claude(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: mystery\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "codex")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"


class TestResolveAiModel:
    def test_codex_defaults_use_gpt_5_5(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert (
            ai_backend.resolve_ai_model("codex-cli", model_tier="small", vault=vault)
            == "gpt-5.5"
        )
        assert (
            ai_backend.resolve_ai_model("codex-cli", model_tier="large", vault=vault)
            == "gpt-5.5"
        )

    def test_grok_defaults_use_grok_4_6(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert (
            ai_backend.resolve_ai_model("grok-cli", model_tier="small", vault=vault)
            == "grok-4.6"
        )
        assert (
            ai_backend.resolve_ai_model("grok-cli", model_tier="large", vault=vault)
            == "grok-4.6"
        )

    def test_configured_grok_models_override_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai_models:\n  grok:\n    small: grok-fast\n    large: grok-pro\n",
        )

        assert (
            ai_backend.resolve_ai_model("grok-cli", model_tier="small", vault=vault)
            == "grok-fast"
        )
        assert (
            ai_backend.resolve_ai_model("grok-cli", model_tier="large", vault=vault)
            == "grok-pro"
        )

    def test_claude_defaults_are_tiered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert (
            ai_backend.resolve_ai_model("claude-cli", model_tier="small", vault=vault)
            == "claude-haiku-4-5-20251001"
        )
        assert (
            ai_backend.resolve_ai_model("claude-cli", model_tier="large", vault=vault)
            == "claude-sonnet-4-6"
        )

    def test_configured_claude_models_override_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai_models:\n"
            "  claude:\n"
            "    small: claude-custom-haiku\n"
            "    large: claude-custom-sonnet\n",
        )

        assert (
            ai_backend.resolve_ai_model("claude-cli", model_tier="small", vault=vault)
            == "claude-custom-haiku"
        )
        assert (
            ai_backend.resolve_ai_model("claude-cli", model_tier="large", vault=vault)
            == "claude-custom-sonnet"
        )

    def test_configured_codex_models_override_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai_models:\n  codex:\n    small: gpt-5.5-mini\n    large: gpt-5.5-pro\n",
        )

        assert (
            ai_backend.resolve_ai_model("codex-cli", model_tier="small", vault=vault)
            == "gpt-5.5-mini"
        )
        assert (
            ai_backend.resolve_ai_model("codex-cli", model_tier="large", vault=vault)
            == "gpt-5.5-pro"
        )

    def test_explicit_model_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert (
            ai_backend.resolve_ai_model(
                "codex-cli", model=" custom-model ", model_tier="large", vault=vault
            )
            == "custom-model"
        )

    def test_none_backend_has_no_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert (
            ai_backend.resolve_ai_model("none", model_tier="large", vault=vault) is None
        )


class TestRunAiPrompt:
    def test_none_backend_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: none\n")

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_claude_command_construction_and_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: claude-cli\nclaude_cli:\n  minimal_context: false\n",
        )
        monkeypatch.setenv("CLAUDECODE", "1")
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"result": "answer\\n"}', stderr=""
            )

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)

        result = ai_backend.run_ai_prompt(
            "hello", model_tier="small", timeout=12, cwd=tmp_path, vault=vault
        )

        assert result == "answer"
        assert calls
        cmd, kwargs = calls[0]
        # SEC-123: prompt is passed on stdin, NOT as a positional argv element.
        assert cmd == [
            "claude",
            "-p",
            "--model",
            "claude-haiku-4-5-20251001",
            "--no-session-persistence",
            "--output-format",
            "json",
        ]
        assert kwargs["stdin"] == "hello"
        assert kwargs["timeout"] == 12
        assert kwargs["cwd"] == str(tmp_path)
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["PARSIDION_INTERNAL"] == "1"
        assert "CLAUDECODE" not in env

    def test_claude_plain_stdout_fallback_returns_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: claude-cli\n")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout="raw answer\n", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)

        # Non-JSON stdout (older CLI / pre-JSON output) falls back to raw text.
        assert ai_backend.run_ai_prompt("hello", vault=vault) == "raw answer"

    def test_claude_minimal_context_overrides_system_prompt_and_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default minimal_context: --system-prompt override + clean scratch cwd.

        claude -p otherwise ingests the project's CLAUDE.md chain, which is
        dead context (and an injection surface) for pure text-transform
        prompts. Verified live: CLAUDE.md instructions leak into replies
        without the override and disappear with it.
        """
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: claude-cli\n")
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"result": "answer"}', stderr=""
            )

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)

        assert ai_backend.run_ai_prompt("hello", cwd=tmp_path, vault=vault) == "answer"
        cmd, kwargs = calls[0]
        assert (
            cmd[cmd.index("--system-prompt") + 1] == ai_backend._MINIMAL_SYSTEM_PROMPT
        )
        assert kwargs["cwd"] == str(ai_backend._minimal_context_cwd())
        assert kwargs["cwd"] != str(tmp_path)

    def test_claude_cli_timeout_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: claude-cli\nclaude_cli:\n  timeout: 44\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"result": "answer"}', stderr=""
            )

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)

        assert ai_backend.run_ai_prompt("hello", vault=vault) == "answer"
        assert calls[0][1]["timeout"] == 44

    def test_timeout_config_inf_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEC-024: .inf in config must not become a wait-forever subprocess."""
        import vault_config

        vault = _reset_config(monkeypatch, tmp_path, "claude_cli:\n  timeout: .inf\n")
        assert (
            ai_backend._config_timeout("claude_cli", "timeout", 30, vault=vault) == 30
        )
        # Unit-level clamp coverage.
        ct = vault_config.clamp_timeout
        assert ct(float("nan"), 30) == 30
        assert ct(float("-inf"), 30) == 30
        assert ct(-5, 30) == 30
        assert ct(0, 30) == 30
        assert ct(True, 30) == 30
        assert ct("60", 30) == 30  # type: ignore[arg-type] # non-numeric -> default
        assert ct(60, 30) == 60
        assert ct(99_999, 30) == 3600  # clamped to hi
        assert ct(1, 30) == 1

    def test_grok_command_construction_minimal_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: grok-cli\n")
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            prompt_file = Path(cmd[cmd.index("--prompt-file") + 1])
            assert prompt_file.read_text(encoding="utf-8") == "hello"
            return subprocess.CompletedProcess(
                cmd, 0, stdout="grok answer\n", stderr=""
            )

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        result = ai_backend.run_ai_prompt(
            "hello", model_tier="small", timeout=34, cwd=tmp_path, vault=vault
        )

        assert result == "grok answer"
        cmd, kwargs = calls[0]
        assert cmd[0] == "/usr/local/bin/grok"
        # SEC-123: prompt travels via --prompt-file, never argv.
        assert "hello" not in cmd
        assert "--verbatim" in cmd
        assert cmd[cmd.index("--model") + 1] == "grok-4.6"
        # minimal_context (default): hermetic prompt — no CLAUDE/AGENTS
        # ingestion, no tools, no subagents, no web search.
        assert cmd[cmd.index("--system-prompt-override") + 1] == (
            ai_backend._MINIMAL_SYSTEM_PROMPT
        )
        assert cmd[cmd.index("--cwd") + 1] == str(ai_backend._minimal_context_cwd())
        assert cmd[cmd.index("--tools") + 1] == ""
        assert "--no-subagents" in cmd
        assert "--disable-web-search" in cmd
        assert kwargs["timeout"] == 34
        env = kwargs["env"]
        assert env["PARSIDION_INTERNAL"] == "1"
        assert "CLAUDECODE" not in env

    def test_grok_minimal_context_false_still_disables_tools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SEC-202: minimal_context is a context option, not a safety switch.

        With ``minimal_context: false`` (and the default ``allow_tools:
        false``) the system-prompt override is dropped but the tool/subagent/
        web-search flags and the clean scratch cwd must still be emitted —
        previously this config re-armed grok's default tools on prompts
        embedding adversarial transcript/vault content.
        """
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: grok-cli\ngrok_cli:\n  minimal_context: false\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="grok answer", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", cwd=tmp_path, vault=vault) is not None
        cmd, kwargs = calls[0]
        # Only the context option disappears.
        assert "--system-prompt-override" not in cmd
        # Safety flags stay on regardless of minimal_context.
        assert cmd[cmd.index("--cwd") + 1] == str(ai_backend._minimal_context_cwd())
        assert cmd[cmd.index("--tools") + 1] == ""
        assert "--no-subagents" in cmd
        assert "--disable-web-search" in cmd
        assert kwargs["cwd"] == str(tmp_path)
        # Default timeout: grok-4.6 headless measured 17-40s per prompt.
        assert kwargs["timeout"] == ai_backend._DEFAULT_GROK_TIMEOUT

    def test_grok_allow_tools_true_omits_tool_flags_and_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """SEC-202: only the explicit allow_tools opt-in re-arms tools, and it warns."""
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: grok-cli\ngrok_cli:\n  allow_tools: true\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="grok answer", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is not None
        cmd, _kwargs = calls[0]
        assert "--tools" not in cmd
        assert "--no-subagents" not in cmd
        assert "--disable-web-search" not in cmd
        # The scratch cwd and the system-prompt override (minimal_context
        # defaults true) are independent of the tool flags.
        assert cmd[cmd.index("--cwd") + 1] == str(ai_backend._minimal_context_cwd())
        assert cmd[cmd.index("--system-prompt-override") + 1] == (
            ai_backend._MINIMAL_SYSTEM_PROMPT
        )
        captured = capsys.readouterr()
        assert "allow_tools" in captured.err
        assert "WARNING" in captured.err

    def test_grok_cli_config_controls_command_and_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: grok-cli\ngrok_cli:\n  timeout: 45\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="grok answer", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is not None
        assert calls[0][1]["timeout"] == 45

    def test_grok_unresolvable_command_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: grok-cli\n")
        monkeypatch.setattr(ai_backend.shutil, "which", lambda name: None)

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_grok_nonzero_exit_and_empty_output_return_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: grok-cli\n")
        outcomes = [
            subprocess.CompletedProcess([], 2, stdout="", stderr="boom"),
            subprocess.CompletedProcess([], 0, stdout="  \n", stderr=""),
        ]
        monkeypatch.setattr(
            ai_backend,
            "_run_prompt_subprocess",
            lambda cmd, **kwargs: outcomes.pop(0),
        )
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_claude_empty_json_result_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: claude-cli\n")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"result": ""}', stderr=""
            )

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_codex_command_construction_reads_output_last_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "not-for-codex")
        calls: list[tuple[list[str], dict[str, Any]]] = []
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            output_path.write_text("codex answer\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="stream noise", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: bare "codex" command resolves via shutil.which.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        result = ai_backend.run_ai_prompt(
            "hello", model_tier="large", timeout=34, cwd=tmp_path, vault=vault
        )

        assert result == "codex answer"
        assert calls
        cmd, kwargs = calls[0]
        # SEC-117: command resolved via shutil.which before launching.
        assert cmd[0] == "/usr/local/bin/codex"
        assert cmd[1] == "exec"
        assert cmd[cmd.index("--config") + 1] == "notify=[]"
        assert "--ephemeral" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert "--skip-git-repo-check" in cmd
        assert "--output-last-message" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
        # SEC-123: prompt is passed on stdin, NOT as the final cmd positional.
        assert "hello" not in cmd
        assert kwargs["stdin"] == "hello"
        assert kwargs["timeout"] == 34
        assert kwargs["cwd"] == str(tmp_path)
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["PARSIDION_INTERNAL"] == "1"
        assert env["CODEX_HOME"] == str(tmp_path / ".codex")
        assert "ANTHROPIC_API_KEY" not in env
        assert output_paths and not output_paths[0].exists()

    def test_codex_cli_config_controls_command_timeout_and_safety_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n"
            "  backend: codex-cli\n"
            "codex_cli:\n"
            "  command: custom-codex\n"
            "  timeout: 45\n"
            "  sandbox: null\n"
            "  ephemeral: false\n"
            "  skip_git_repo_check: false\n"
            "  suppress_notify: false\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("configured answer", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: bare command resolves via shutil.which.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) == "configured answer"
        cmd, kwargs = calls[0]
        # Resolved path replaces the configured bare name.
        assert cmd[:2] == ["/usr/local/bin/custom-codex", "exec"]
        assert "--config" not in cmd
        assert "--ephemeral" not in cmd
        assert "--sandbox" not in cmd
        assert "--skip-git-repo-check" not in cmd
        assert kwargs["timeout"] == 45

    def test_codex_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("ignored", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_codex_empty_output_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("  \n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_codex_timeout_returns_none_and_deletes_output_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            output_path.write_text("partial", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert output_paths and not output_paths[0].exists()

    def test_codex_timeout_can_raise_opt_in_exception_and_deletes_output_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            output_path.write_text("partial", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        with pytest.raises(ai_backend.AiBackendTimeout):
            ai_backend.run_ai_prompt("hello", vault=vault, raise_on_timeout=True)
        assert output_paths and not output_paths[0].exists()

    def test_codex_timeout_escalates_process_group_kill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SEC-122 / ARC-048f: the killpg/escalation logic moved to
        # ``subproc_util.run_with_pgkill`` and is tested there. Here we
        # only verify the contract: a timeout returns None and leaves no
        # output file behind. The detailed SIGTERM/SIGKILL ordering is
        # exercised in ``tests/test_subproc_util.py``.
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            output_path.write_text("partial", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert output_paths and not output_paths[0].exists()

    def test_codex_success_with_missing_output_file_returns_none_and_cleans_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            output_path.unlink()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert output_paths and not output_paths[0].exists()

    def test_codex_oserror_returns_none_and_deletes_output_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        output_paths: list[Path] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_paths.append(output_path)
            raise FileNotFoundError("codex")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        # SEC-117: command resolution via shutil.which — gate must pass so the
        # (mocked) subprocess logic is exercised even where codex isn't installed.
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert output_paths and not output_paths[0].exists()


class TestSec117CodexCommandGate:
    """SEC-117: ``codex_cli.command`` is gated via shutil.which / file checks."""

    def test_bare_command_resolves_via_path(self, tmp_path: Path, monkeypatch) -> None:
        # Bare "codex" must resolve via shutil.which.
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )
        ai_backend.run_ai_prompt("hi", vault=vault)
        assert captured[0] == "/usr/local/bin/codex"

    def test_unresolvable_bare_command_returns_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        # shutil.which returns None — command is unresolvable.
        monkeypatch.setattr(ai_backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            ai_backend,
            "_run_prompt_subprocess",
            lambda *a, **kw: pytest.fail("should not launch"),
        )
        # Must NOT raise; must NOT launch — returns None and the caller falls back.
        assert ai_backend.run_ai_prompt("hi", vault=vault) is None

    def test_path_command_must_exist(self, tmp_path: Path, monkeypatch) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: codex-cli\ncodex_cli:\n  command: /nonexistent/codex\n",
        )
        monkeypatch.setattr(
            ai_backend,
            "_run_prompt_subprocess",
            lambda *a, **kw: pytest.fail("should not launch"),
        )
        assert ai_backend.run_ai_prompt("hi", vault=vault) is None

    def test_existing_executable_path_command_is_used(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Create an executable file at an absolute path; verify it's used as argv[0].
        custom = tmp_path / "my-codex"
        custom.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        custom.chmod(0o755)
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            f"ai:\n  backend: codex-cli\ncodex_cli:\n  command: {custom}\n",
        )
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        ai_backend.run_ai_prompt("hi", vault=vault)
        assert captured[0] == str(custom)


class TestSec007ConfiguredBinaryTrustGate:
    """SEC-007: path-like configured commands must pass is_trusted_executable.

    A synced config.yaml must not be able to point a backend at a binary the
    current user does not own or that group/world can write. On refusal the
    backend falls back to its default command name resolved from PATH.
    """

    def _untrusted_binary(self, tmp_path: Path) -> Path:
        evil = tmp_path / "evil-codex"
        evil.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        evil.chmod(0o777)  # group+world writable -> untrusted
        return evil

    def test_untrusted_command_falls_back_to_default(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        evil = self._untrusted_binary(tmp_path)
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            f"ai:\n  backend: codex-cli\ncodex_cli:\n  command: {evil}\n",
        )
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )
        ai_backend.run_ai_prompt("hi", vault=vault)
        assert captured[0] == "/usr/local/bin/codex"
        assert "SEC-007" in capsys.readouterr().err

    def test_untrusted_command_without_fallback_does_not_launch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        evil = self._untrusted_binary(tmp_path)
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            f"ai:\n  backend: codex-cli\ncodex_cli:\n  command: {evil}\n",
        )
        monkeypatch.setattr(ai_backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(
            ai_backend,
            "_run_prompt_subprocess",
            lambda *a, **kw: pytest.fail("should not launch"),
        )
        assert ai_backend.run_ai_prompt("hi", vault=vault) is None

    def test_owned_nonwritable_command_still_used(self, tmp_path: Path) -> None:
        # The pre-existing SEC-117 semantics: an owned, non-writable custom
        # path is trusted and stays the launch target.
        custom = tmp_path / "my-codex"
        custom.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        custom.chmod(0o755)
        resolved = ai_backend._resolve_configured_binary(
            str(custom), "codex_cli.command", "codex"
        )
        assert resolved == str(custom)


class TestSec117CodexSandboxAllowlist:
    """SEC-117: ``danger-full-access`` requires explicit opt-in."""

    def test_danger_full_access_refused_by_default(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: codex-cli\ncodex_cli:\n  sandbox: danger-full-access\n",
        )
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )
        ai_backend.run_ai_prompt("hi", vault=vault)
        # Refused → replaced with read-only.
        assert "--sandbox" in captured
        assert captured[captured.index("--sandbox") + 1] == "read-only"

    def test_danger_full_access_allowed_with_explicit_opt_in(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai:\n  backend: codex-cli\n"
            "codex_cli:\n"
            "  sandbox: danger-full-access\n"
            "  allow_danger_full_access: true\n",
        )
        captured: list[str] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ai_backend, "_run_prompt_subprocess", fake_run)
        monkeypatch.setattr(
            ai_backend.shutil, "which", lambda name: f"/usr/local/bin/{name}"
        )
        ai_backend.run_ai_prompt("hi", vault=vault)
        assert "--sandbox" in captured
        assert captured[captured.index("--sandbox") + 1] == "danger-full-access"
