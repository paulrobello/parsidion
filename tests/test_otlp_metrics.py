"""Tests for core/otlp_metrics.py — config-gated OTLP usage telemetry."""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from core import otlp_metrics
from core.otlp_metrics import (
    OtlpSettings,
    build_usage_payload,
    emit_usage_metric,
    load_otlp_settings,
    parse_otlp_headers,
    post_metrics,
)


class _CaptureHandler(BaseHTTPRequestHandler):
    """Records the last request; answers 200, or 500 when told to fail."""

    server_version = "TestOTLP"
    fail = False
    last: dict[str, Any] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _CaptureHandler.last = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "auth": self.headers.get("Authorization"),
            "body": self.rfile.read(length),
        }
        if _CaptureHandler.fail:
            self.send_response(500)
        else:
            self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def capture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _CaptureHandler.fail = False
    _CaptureHandler.last = {}
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/metrics"
    server.shutdown()
    server.server_close()


def _settings(url: str, **overrides: Any) -> OtlpSettings:
    defaults: dict[str, Any] = {
        "endpoint": url,
        "headers": {"Authorization": "Bearer test-token"},
        "timeout_s": 2.0,
        "wait_s": 2.0,
        "service_name": "parsidion",
    }
    defaults.update(overrides)
    return OtlpSettings(**defaults)


class TestParseHeaders:
    def test_plain_and_url_quoted(self) -> None:
        headers = parse_otlp_headers(
            "Authorization=Bearer%20x%3D1,CF-Access-Client-Id=abc.access"
        )
        assert headers == {
            "Authorization": "Bearer x=1",
            "CF-Access-Client-Id": "abc.access",
        }

    def test_malformed_pairs_skipped(self) -> None:
        assert parse_otlp_headers("noequals,=emptykey,=,ok=v") == {"ok": "v"}

    def test_empty(self) -> None:
        assert parse_otlp_headers("") == {}


class TestLoadSettings:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(otlp_metrics, "get_config", lambda *a, **k: False)
        assert load_otlp_settings() is None

    def test_enabled_without_endpoint_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_get(section: str, key: str, default: Any) -> Any:
            return {"enabled": True, "endpoint": ""}.get(key, default)

        monkeypatch.setattr(otlp_metrics, "get_config", fake_get)
        assert load_otlp_settings() is None

    def test_enabled_resolves_full_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_get(section: str, key: str, default: Any) -> Any:
            values = {
                "enabled": True,
                "endpoint": "https://obs.example/v1/metrics",
                "headers": "Authorization=Bearer%20tok",
                "service_name": "parsidion-test",
            }
            return values.get(key, default)

        monkeypatch.setattr(otlp_metrics, "get_config", fake_get)
        settings = load_otlp_settings()
        assert settings is not None
        assert settings.endpoint == "https://obs.example/v1/metrics"
        assert settings.headers == {"Authorization": "Bearer tok"}
        assert settings.service_name == "parsidion-test"
        assert settings.timeout_s == 2.0
        assert settings.wait_s == 0.5


class TestPayload:
    def test_shape(self) -> None:
        payload = build_usage_payload(
            "parsidion_hook_events",
            1.0,
            {"hook": "SessionEnd", "project": "parsidion"},
            "parsidion",
        )
        rm = payload["resourceMetrics"]
        assert len(rm) == 1
        attrs = {
            a["key"]: a["value"]["stringValue"] for a in rm[0]["resource"]["attributes"]
        }
        assert attrs["service.name"] == "parsidion"
        assert attrs["host.name"]
        metric = rm[0]["scopeMetrics"][0]["metrics"][0]
        assert metric["name"] == "parsidion_hook_events"
        point = metric["gauge"]["dataPoints"][0]
        assert point["asDouble"] == 1.0
        assert isinstance(point["timeUnixNano"], str)
        pt_attrs = {a["key"]: a["value"]["stringValue"] for a in point["attributes"]}
        assert pt_attrs == {"hook": "SessionEnd", "project": "parsidion"}

    def test_no_attrs_omits_key(self) -> None:
        payload = build_usage_payload("m", 2.5, {}, "svc")
        point = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["gauge"][
            "dataPoints"
        ][0]
        assert "attributes" not in point
        assert point["asDouble"] == 2.5


class TestPost:
    def test_delivers_body_and_headers(self, capture_server: str) -> None:
        body = json.dumps(build_usage_payload("m", 1.0, {"k": "v"}, "svc")).encode()
        status = post_metrics(capture_server, body, {"Authorization": "Bearer t"}, 2.0)
        assert status == 200
        sent = _CaptureHandler.last
        assert sent["content_type"] == "application/json"
        assert sent["auth"] == "Bearer t"
        assert json.loads(sent["body"])["resourceMetrics"]

    def test_http_500_raises(self, capture_server: str) -> None:
        _CaptureHandler.fail = True
        body = b"{}"
        with pytest.raises(urllib.error.HTTPError):
            post_metrics(capture_server, body, {}, 2.0)


class TestEmit:
    def test_off_by_default_no_network(self) -> None:
        assert emit_usage_metric("m", settings=None) is False

    def test_delivers_within_wait(self, capture_server: str) -> None:
        settings = _settings(capture_server)
        assert (
            emit_usage_metric(
                "parsidion_hook_events", 1.0, {"hook": "X"}, settings=settings
            )
            is True
        )
        assert json.loads(_CaptureHandler.last["body"])["resourceMetrics"]

    def test_server_error_returns_false(self, capture_server: str) -> None:
        _CaptureHandler.fail = True
        settings = _settings(capture_server)
        assert emit_usage_metric("m", settings=settings) is False

    def test_never_raises_on_garbage_settings(self) -> None:
        bad = OtlpSettings(endpoint="blackhole://x", timeout_s=0.1, wait_s=0.1)
        assert emit_usage_metric("m", settings=bad) is False


class TestHookEventIntegration:
    def test_write_hook_event_emits_one_datapoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One emit per hook event, with hook+project attrs, never blocking."""
        from core import vault_fs as vault_fs_mod

        captured: list[tuple[str, float, dict[str, str]]] = []

        def fake_emit(name: str, value: float, attrs: dict[str, str]) -> bool:
            captured.append((name, value, attrs))
            return True

        monkeypatch.setattr(vault_fs_mod, "emit_usage_metric", fake_emit)
        from core.vault_fs import write_hook_event

        log = tmp_path / "hook_events.log"
        write_hook_event("SessionEnd", "parsidion", 12.5, vault=tmp_path)
        assert log.exists()
        assert captured == [
            (
                "parsidion_hook_events",
                1.0,
                {"hook": "SessionEnd", "project": "parsidion"},
            )
        ]
        # The local event-log write also still happened.
        assert (
            json.loads(log.read_text().strip().splitlines()[0])["hook"] == "SessionEnd"
        )

    def test_emit_failure_never_breaks_event_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: object, **k: object) -> bool:
            raise RuntimeError("otlp down")

        from core import vault_fs as vault_fs_mod

        monkeypatch.setattr(vault_fs_mod, "emit_usage_metric", boom)
        from core.vault_fs import write_hook_event

        write_hook_event("SessionStart", "parsidion", 5.0, vault=tmp_path)
        log = tmp_path / "hook_events.log"
        assert log.exists()
        lines = log.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["hook"] == "SessionStart"
