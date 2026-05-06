"""Lightweight FastAPI smoke + telemetry coverage for ``app.webapp``."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from app import webapp


def test_webapp_module_calls_init_sentry_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    init_mock = MagicMock()
    monkeypatch.setattr("app.utils.sentry_sdk.init_sentry", init_mock)

    importlib.reload(webapp)

    init_mock.assert_called_once()


def test_health_response_returns_known_fields() -> None:
    response = webapp.get_health_response()

    assert hasattr(response, "ok")
    assert hasattr(response, "version")
    assert hasattr(response, "graph_loaded")
    assert hasattr(response, "llm_configured")
    assert hasattr(response, "env")
