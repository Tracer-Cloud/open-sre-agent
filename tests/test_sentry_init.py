from __future__ import annotations

import sys
import warnings
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.constants import SENTRY_DSN, SENTRY_ERROR_SAMPLE_RATE, SENTRY_TRACES_SAMPLE_RATE
from app.utils import sentry_sdk as sentry_mod


@pytest.fixture(autouse=True)
def _reset_sentry_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear cached init/tag state, and skip building real integrations.

    The default test pattern replaces ``sys.modules["sentry_sdk"]`` with a
    ``SimpleNamespace`` stub, which breaks ``from sentry_sdk.integrations.X
    import Y`` because the stub is not a package. Stubbing the integrations
    builder with an empty list keeps every test working; the one test that
    asserts integrations were wired overrides this in its own body.
    """
    sentry_mod._init_sentry_once.cache_clear()
    sentry_mod._reset_scope_tags_state_for_tests()
    monkeypatch.setattr(sentry_mod, "_build_sentry_integrations", lambda: [])


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


def test_capture_exception_attaches_context(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    capture_mock = MagicMock()
    tags: dict[str, str] = {}
    extras: dict[str, object] = {}

    class _Scope:
        def __enter__(self) -> _Scope:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def set_tag(self, key: str, value: str) -> None:
            tags[key] = value

        def set_extra(self, key: str, value: object) -> None:
            extras[key] = value

    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(capture_exception=capture_mock, push_scope=_Scope),
    )

    sentry_mod.capture_exception(
        ValueError("boom"),
        context="interactive_shell.cli_agent.stream",
        extra={"turn": 3},
    )

    capture_mock.assert_called_once()
    assert tags == {"opensre.context": "interactive_shell.cli_agent.stream"}
    assert extras == {"turn": 3}


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


def _clear_kill_switches(monkeypatch) -> None:
    for env in (
        "OPENSRE_SENTRY_DISABLED",
        "OPENSRE_NO_TELEMETRY",
        "DO_NOT_TRACK",
        "OPENSRE_SENTRY_DSN",
        "SENTRY_DSN",
    ):
        monkeypatch.delenv(env, raising=False)


def _install_full_sentry_mock(monkeypatch):
    init_mock = MagicMock()
    tag_mock = MagicMock()
    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(init=init_mock, set_tag=tag_mock),
    )
    return init_mock, tag_mock


def test_init_sentry_passes_explicit_integrations(monkeypatch) -> None:
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.httpx import HttpxIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    real_integrations = [
        LoggingIntegration(),
        AsyncioIntegration(),
        HttpxIntegration(),
    ]
    monkeypatch.setattr(sentry_mod, "_build_sentry_integrations", lambda: real_integrations)
    _clear_kill_switches(monkeypatch)
    init_mock, _ = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    integrations = init_mock.call_args.kwargs["integrations"]
    integration_names = {type(integration).__name__ for integration in integrations}
    assert "LoggingIntegration" in integration_names
    assert "AsyncioIntegration" in integration_names
    assert "HttpxIntegration" in integration_names


def test_init_sentry_suppresses_langgraph_allowed_objects_warning(monkeypatch) -> None:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    _clear_kill_switches(monkeypatch)
    init_mock, _ = _install_full_sentry_mock(monkeypatch)

    init_mock.side_effect = lambda **_kwargs: warnings.warn(
        (
            "The default value of `allowed_objects` will change in a future version. "
            "Pass an explicit value (e.g., allowed_objects='messages' or "
            "allowed_objects='core') to suppress this warning."
        ),
        category=LangChainPendingDeprecationWarning,
        stacklevel=1,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        sentry_mod.init_sentry(entrypoint="cli")

    assert init_mock.call_count == 1
    assert not [
        warning
        for warning in caught
        if isinstance(warning.message, LangChainPendingDeprecationWarning)
    ]


def test_init_sentry_sets_in_app_include_app(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    init_mock, _ = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    assert init_mock.call_args.kwargs["in_app_include"] == ["app"]


def test_init_sentry_sets_max_breadcrumbs(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    init_mock, _ = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    assert init_mock.call_args.kwargs["max_breadcrumbs"] == 100


def test_init_sentry_sets_scope_tags(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    monkeypatch.setenv("OPENSRE_DEPLOYMENT_METHOD", "railway")
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="webapp")

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("entrypoint", "webapp") in tag_calls
    assert ("opensre.runtime", "hosted") in tag_calls
    assert ("deployment_method", "railway") in tag_calls


def test_init_sentry_entrypoint_defaults_to_unknown(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry()

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("entrypoint", "unknown") in tag_calls


def test_init_sentry_runtime_tag_is_cli_for_cli_entrypoint(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("opensre.runtime", "cli") in tag_calls


def test_init_sentry_runtime_tag_is_hosted_for_webapp_entrypoint(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="webapp")

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("opensre.runtime", "hosted") in tag_calls


def test_init_sentry_runtime_tag_is_cli_when_entrypoint_unknown(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry()

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("opensre.runtime", "cli") in tag_calls


def test_init_sentry_deployment_method_defaults_to_local(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    monkeypatch.delenv("OPENSRE_DEPLOYMENT_METHOD", raising=False)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    tag_calls = {call.args for call in tag_mock.call_args_list}
    assert ("deployment_method", "local") in tag_calls


def test_before_send_filters_request_body_recursively() -> None:
    event = {
        "request": {
            "data": {
                "system_prompt": "ignore previous instructions",
                "nested": {"bearer": "ghp_xxx", "user_id": "ok"},
            },
        },
    }

    sentry_mod._before_send(event, {})

    data = event["request"]["data"]
    assert data["system_prompt"] == "[Filtered]"
    assert data["nested"]["bearer"] == "[Filtered]"
    assert data["nested"]["user_id"] == "ok"


def test_before_send_filters_request_body_with_substring_match() -> None:
    event = {
        "request": {
            "body": {
                "messages": [{"role": "user", "content": "hi"}],
                "chat_messages_v2": "blob",
                "app_dsn": "https://abc@sentry.invalid/1",
                "request_id": "req-42",
            },
        },
    }

    sentry_mod._before_send(event, {})

    body = event["request"]["body"]
    assert body["messages"] == "[Filtered]"
    assert body["chat_messages_v2"] == "[Filtered]"
    assert body["app_dsn"] == "[Filtered]"
    assert body["request_id"] == "req-42"


def test_before_send_filters_extra_keys_substring_match() -> None:
    event = {
        "extra": {
            "prompt": "do bad things",
            "chat_messages": "blob",
            "bearer_session": "abc",
            "user_credential_email": "x",
            "user_email": "user@example.com",
            "request_id": "req-42",
        },
    }

    sentry_mod._before_send(event, {})

    extra = event["extra"]
    assert extra["prompt"] == "[Filtered]"
    assert extra["chat_messages"] == "[Filtered]"
    assert extra["bearer_session"] == "[Filtered]"
    assert extra["user_credential_email"] == "[Filtered]"
    assert extra["user_email"] == "user@example.com"
    assert extra["request_id"] == "req-42"


def test_before_breadcrumb_filters_http_headers() -> None:
    crumb = {
        "category": "httpx",
        "data": {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=abc",
                "User-Agent": "opensre/1",
            },
        },
    }

    sentry_mod._before_breadcrumb(crumb, {})

    headers = crumb["data"]["headers"]
    assert headers["Authorization"] == "[Filtered]"
    assert headers["Cookie"] == "[Filtered]"
    assert headers["User-Agent"] == "opensre/1"


def test_before_breadcrumb_filters_aiohttp_headers() -> None:
    crumb = {
        "category": "aiohttp",
        "data": {"headers": {"authorization": "Bearer xyz", "X-Trace": "ok"}},
    }

    sentry_mod._before_breadcrumb(crumb, {})

    headers = crumb["data"]["headers"]
    assert headers["authorization"] == "[Filtered]"
    assert headers["X-Trace"] == "ok"


def test_before_breadcrumb_does_not_touch_console_headers() -> None:
    crumb = {
        "category": "console",
        "data": {"headers": {"Authorization": "Bearer secret"}},
    }

    sentry_mod._before_breadcrumb(crumb, {})

    assert crumb["data"]["headers"]["Authorization"] == "Bearer secret"


def test_before_send_filters_nested_lists_of_dicts() -> None:
    event = {
        "request": {
            "data": {
                "batch": [[{"prompt": "leak"}, {"safe": "ok"}], [{"bearer": "x"}]],
            },
        },
    }

    sentry_mod._before_send(event, {})

    nested = event["request"]["data"]["batch"]
    assert nested[0][0]["prompt"] == "[Filtered]"
    assert nested[0][1]["safe"] == "ok"
    assert nested[1][0]["bearer"] == "[Filtered]"


def test_init_sentry_skips_scope_tags_when_dsn_empty(monkeypatch) -> None:
    sentry_mod._init_sentry_once.cache_clear()
    _clear_kill_switches(monkeypatch)
    monkeypatch.setenv("OPENSRE_SENTRY_DSN", "")
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setattr(sentry_mod, "SENTRY_DSN", "")
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="cli")

    tag_mock.assert_not_called()


def test_before_send_filters_extra_recursively() -> None:
    event = {
        "extra": {
            "context": {
                "auth_token": "ghp_xxx",
                "messages": [{"role": "user", "content": "hi"}],
                "user_id": "ok",
            },
            "request_id": "req-42",
        },
    }

    sentry_mod._before_send(event, {})

    extra = event["extra"]
    assert extra["context"]["auth_token"] == "[Filtered]"
    assert extra["context"]["messages"] == "[Filtered]"
    assert extra["context"]["user_id"] == "ok"
    assert extra["request_id"] == "req-42"


def test_before_send_parses_json_string_request_body() -> None:
    raw_body = (
        '{"system_prompt": "you are an assistant",'
        ' "messages": [{"role": "user", "content": "hi"}],'
        ' "request_id": "req-1"}'
    )
    event = {"request": {"body": raw_body}}

    sentry_mod._before_send(event, {})

    body = event["request"]["body"]
    assert isinstance(body, dict)
    assert body["system_prompt"] == "[Filtered]"
    assert body["messages"] == "[Filtered]"
    assert body["request_id"] == "req-1"


def test_before_send_leaves_non_json_request_body_string_alone() -> None:
    event = {"request": {"body": "not json"}}

    sentry_mod._before_send(event, {})

    assert event["request"]["body"] == "not json"


def test_init_sentry_does_not_double_init_across_entrypoints(monkeypatch) -> None:
    _clear_kill_switches(monkeypatch)
    init_mock, _ = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="webapp")
    sentry_mod.init_sentry(entrypoint="graph_pipeline")

    init_mock.assert_called_once()


def test_apply_scope_tags_is_first_wins(monkeypatch) -> None:
    _clear_kill_switches(monkeypatch)
    _, tag_mock = _install_full_sentry_mock(monkeypatch)

    sentry_mod.init_sentry(entrypoint="webapp")
    sentry_mod.init_sentry(entrypoint="graph_pipeline")

    entrypoint_tags = [
        call.args[1] for call in tag_mock.call_args_list if call.args[0] == "entrypoint"
    ]
    assert entrypoint_tags == ["webapp"]
