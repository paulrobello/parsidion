from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ai_backend  # noqa: E402
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
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: claude-cli\n")
        monkeypatch.setenv("CLAUDECODE", "1")
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="answer\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ai_backend.run_ai_prompt(
            "hello", model_tier="small", timeout=12, cwd=tmp_path, vault=vault
        )

        assert result == "answer"
        assert calls
        cmd, kwargs = calls[0]
        assert cmd == [
            "claude",
            "-p",
            "hello",
            "--model",
            "claude-haiku-4-5-20251001",
            "--no-session-persistence",
        ]
        assert kwargs["timeout"] == 12
        assert kwargs["cwd"] == str(tmp_path)
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["PARSIDION_INTERNAL"] == "1"
        assert "CLAUDECODE" not in env

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

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ai_backend.run_ai_prompt(
            "hello", model_tier="large", timeout=34, cwd=tmp_path, vault=vault
        )

        assert result == "codex answer"
        assert calls
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert "--skip-git-repo-check" in cmd
        assert "--output-last-message" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
        assert cmd[-1] == "hello"
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
            "  skip_git_repo_check: false\n",
        )
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append((cmd, kwargs))
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("configured answer", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ai_backend.run_ai_prompt("hello", vault=vault) == "configured answer"
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["custom-codex", "exec"]
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

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None

    def test_codex_empty_output_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("  \n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

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

        monkeypatch.setattr(subprocess, "run", fake_run)

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

        monkeypatch.setattr(subprocess, "run", fake_run)

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

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
        assert output_paths and not output_paths[0].exists()
