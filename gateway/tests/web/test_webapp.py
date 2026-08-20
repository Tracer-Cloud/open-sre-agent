"""Lightweight FastAPI smoke + telemetry coverage for ``gateway.web.webapp``."""

from __future__ import annotations

import importlib
import sys
from http import HTTPStatus
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.web import webapp


def test_webapp_module_calls_init_sentry_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: importing the module runs shared process boot, which is idempotent
    # per profile — an earlier import in this session already consumed it, so a
    # reload alone would assert against a no-op.
    from bootstrap.process import reset_process_runtime_for_tests

    init_mock = MagicMock()
    monkeypatch.setattr("platform.observability.errors.sentry.init_sentry", init_mock)
    reset_process_runtime_for_tests()

    # Act
    importlib.reload(webapp)

    # Assert: the web entrypoint still reports crashes, now via WEB_PROFILE
    # rather than a direct call.
    init_mock.assert_called_once()


def test_webapp_imports_after_stdlib_platform_cached() -> None:
    """Docker/uvicorn can cache stdlib ``platform`` before loading the ASGI app.

    Runs in a fresh interpreter: in-process eviction of ``platform.*`` orphans
    imports already bound by other tests in this pytest worker.
    """
    import subprocess
    import textwrap
    from pathlib import Path

    code = textwrap.dedent(
        """
        import importlib.util
        import sys
        import sysconfig
        from pathlib import Path

        stdlib_path = Path(sysconfig.get_path("stdlib")) / "platform.py"
        spec = importlib.util.spec_from_file_location("_opensre_test_stdlib_platform", stdlib_path)
        assert spec is not None and spec.loader is not None
        stdlib_platform = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stdlib_platform)
        assert not hasattr(stdlib_platform, "__path__")
        sys.modules["platform"] = stdlib_platform
        [sys.modules.pop(n) for n in list(sys.modules) if n.startswith("platform.")]

        from gateway.web import webapp
        assert hasattr(webapp, "app")
        assert hasattr(sys.modules["platform"], "__path__")
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_health_response_returns_known_fields() -> None:
    response = webapp.get_health_response()

    assert hasattr(response, "ok")
    assert hasattr(response, "version")
    assert hasattr(response, "llm_configured")
    assert hasattr(response, "env")


def test_ok_route_is_registered() -> None:
    client = TestClient(webapp.app)
    resp = client.get("/ok")
    assert resp.status_code in (
        HTTPStatus.OK,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )
    data = resp.json()
    assert "ok" in data
    assert "version" in data
