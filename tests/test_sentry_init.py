from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.constants import SENTRY_DSN, SENTRY_ERROR_SAMPLE_RATE, SENTRY_TRACES_SAMPLE_RATE
from app.utils import sentry_sdk as sentry_mod


def test_init_sentry_noops_when_disabled(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.setenv("OPENSRE_SENTRY_DISABLED", "1")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    init_mock.assert_not_called()


def test_init_sentry_is_idempotent_for_same_config(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("SENTRY_ERROR_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("ENV", "production")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()
    sentry_mod.init_sentry()

    init_mock.assert_called_once()
    assert init_mock.call_args.kwargs["dsn"] == SENTRY_DSN
    assert init_mock.call_args.kwargs["environment"] == "production"
    assert init_mock.call_args.kwargs["send_default_pii"] is False
    assert init_mock.call_args.kwargs["attach_stacktrace"] is True
    assert init_mock.call_args.kwargs["sample_rate"] == 0.25
    assert init_mock.call_args.kwargs["traces_sample_rate"] == 0.5


def test_init_sentry_invalid_sample_rate_fallbacks(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("SENTRY_ERROR_SAMPLE_RATE", "invalid_value")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "invalid_value")
    monkeypatch.setenv("ENV", "production")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    init_mock.assert_called_once()
    assert init_mock.call_args.kwargs["sample_rate"] == SENTRY_ERROR_SAMPLE_RATE
    assert init_mock.call_args.kwargs["traces_sample_rate"] == SENTRY_TRACES_SAMPLE_RATE


def test_init_sentry_sample_rates_are_clamped(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("SENTRY_ERROR_SAMPLE_RATE", "2")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "-1")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    assert init_mock.call_args.kwargs["sample_rate"] == 1.0
    assert init_mock.call_args.kwargs["traces_sample_rate"] == 0.0


def test_capture_exception_is_best_effort(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    capture_mock = MagicMock(side_effect=RuntimeError("sentry unavailable"))
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(capture_exception=capture_mock),
    )

    sentry_mod.capture_exception(ValueError("boom"))

    capture_mock.assert_called_once()
