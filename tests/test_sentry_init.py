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
    monkeypatch.delenv("OPENSRE_SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
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


def test_init_sentry_allows_dsn_override(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("OPENSRE_SENTRY_DSN", "https://override@sentry.invalid/1")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    assert init_mock.call_args.kwargs["dsn"] == "https://override@sentry.invalid/1"


def test_init_sentry_invalid_sample_rate_fallbacks(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("OPENSRE_SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
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
    monkeypatch.delenv("OPENSRE_SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
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


def test_init_sentry_noops_when_opensre_no_telemetry(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    init_mock.assert_not_called()


def test_init_sentry_noops_when_do_not_track(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    init_mock.assert_not_called()


def test_init_sentry_dsn_env_overrides_constant(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    custom_dsn = "https://abc@example.ingest.sentry.io/12345"
    monkeypatch.setenv("SENTRY_DSN", custom_dsn)
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    assert init_mock.call_args.kwargs["dsn"] == custom_dsn


def test_init_sentry_release_tag_uses_get_version(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr("app.version.get_version", lambda: "9.9.9")
    init_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "sentry_sdk", SimpleNamespace(init=init_mock))

    sentry_mod.init_sentry()

    assert init_mock.call_args.kwargs["release"] == "opensre@9.9.9"


def test_before_send_filters_sensitive_request_headers() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "session=abc",
                "User-Agent": "opensre/1",
            },
            "cookies": {"session": "abc"},
        },
    }

    sentry_mod._before_send(event, {})

    headers = event["request"]["headers"]
    assert headers["Authorization"] == "[Filtered]"
    assert headers["Cookie"] == "[Filtered]"
    assert headers["User-Agent"] == "opensre/1"
    assert event["request"]["cookies"] == "[Filtered]"


def test_before_send_drops_event_when_dsn_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setattr(sentry_mod, "SENTRY_DSN", "")

    assert sentry_mod._before_send({"message": "boom"}, {}) is None


def test_before_send_filters_sensitive_extra_keys() -> None:
    event = {
        "extra": {
            "github_token": "ghp_xxx",
            "api_key": "abc",
            "user_email": "user@example.com",
        },
    }

    sentry_mod._before_send(event, {})

    assert event["extra"]["github_token"] == "[Filtered]"
    assert event["extra"]["api_key"] == "[Filtered]"
    assert event["extra"]["user_email"] == "user@example.com"


def test_before_send_scrubs_home_paths_in_stack_frames() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "abs_path": "/Users/jane/project/app/foo.py",
                                "vars": {
                                    "path": "/home/runner/secret",
                                    "auth_token": "ghp_xxx",
                                },
                            }
                        ]
                    }
                }
            ]
        }
    }

    sentry_mod._before_send(event, {})

    frame = event["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["abs_path"] == "~/project/app/foo.py"
    assert frame["vars"]["path"] == "~/secret"
    assert frame["vars"]["auth_token"] == "[Filtered]"


def test_before_breadcrumb_strips_query_string_for_http_categories() -> None:
    crumb = {
        "category": "httpx",
        "data": {"url": "https://api.example.com/path?token=secret&id=42"},
    }

    sentry_mod._before_breadcrumb(crumb, {})

    assert crumb["data"]["url"] == "https://api.example.com/path"


def test_before_breadcrumb_leaves_other_categories_alone() -> None:
    crumb = {
        "category": "console",
        "data": {"url": "https://api.example.com/path?token=secret"},
    }

    sentry_mod._before_breadcrumb(crumb, {})

    assert crumb["data"]["url"] == "https://api.example.com/path?token=secret"
