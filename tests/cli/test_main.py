from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import click
import pytest

from app.analytics import provider
from app.analytics.events import Event
from app.cli.__main__ import main
from app.cli.interactive_shell.config import ReplConfig


class _EmptyCatalog:
    def filter(self, *, category: str, search: str) -> list[object]:
        _ = (category, search)
        return []


def _stub_analytics_httpx(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    posted_payloads: list[dict[str, object]] = []

    class _StubResponse:
        def raise_for_status(self) -> None:
            return None

    class _StubClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

        def post(self, url: str, json: dict[str, object]) -> _StubResponse:
            posted_payloads.append({"url": url, "json": json})
            return _StubResponse()

    monkeypatch.setattr(provider.httpx, "Client", _StubClient)
    return posted_payloads


def test_main_runs_health_command(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)

    with (
        patch("app.integrations.verify.verify_integrations") as mock_verify,
        patch("app.integrations.verify.format_verification_results") as mock_format,
    ):
        mock_verify.return_value = [
            {
                "service": "aws",
                "source": "local store",
                "status": "passed",
                "detail": "ok",
            }
        ]
        mock_format.return_value = (
            "\n"
            "  SERVICE    SOURCE       STATUS      DETAIL\n"
            "  aws        local store  passed      ok\n"
        )

        exit_code = main(["health"])

    assert exit_code == 0


def test_main_does_not_capture_expected_usage_errors_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured.append(exc),
    )

    exit_code = main(["integrations", "show", "nonexistent"])

    assert exit_code != 0
    assert captured == []


def test_main_treats_onboard_abort_as_clean_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("app.cli.__main__.init_sentry", lambda **_kw: None)
    monkeypatch.setattr(
        "app.cli.wizard.run_wizard",
        lambda: (_ for _ in ()).throw(click.Abort()),
    )

    exit_code = main(["onboard"])

    assert exit_code == 0


def test_main_allows_update_when_sentry_sdk_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)

    def _raise_missing_sentry(**_kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'sentry_sdk'", name="sentry_sdk")

    monkeypatch.setattr("app.cli.__main__.init_sentry", _raise_missing_sentry)
    monkeypatch.setattr("app.cli.support.update._fetch_latest_version", lambda: "9999.0.0")
    monkeypatch.setattr("app.cli.support.update._is_update_available", lambda _c, _l: False)

    exit_code = main(["update", "--check"])

    assert exit_code == 0
    assert "already up to date" in capsys.readouterr().out


def test_main_non_update_still_raises_when_sentry_sdk_missing(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    def _raise_missing_sentry(**_kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'sentry_sdk'", name="sentry_sdk")

    monkeypatch.setattr("app.cli.__main__.init_sentry", _raise_missing_sentry)

    with pytest.raises(ModuleNotFoundError):
        main(["version"])


def test_main_does_not_capture_analytics_for_help(monkeypatch, capsys) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "app.cli.__main__.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    exit_code = main(["--help"])

    assert exit_code == 0
    assert "Usage:" in capsys.readouterr().out
    assert captured == []


def test_main_does_not_capture_unknown_command_to_sentry(monkeypatch, capsys) -> None:
    captured: list[str] = []
    captured_errors: list[BaseException] = []
    monkeypatch.setattr(
        "app.cli.__main__.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    exit_code = main(["not-a-command"])

    assert exit_code != 0
    assert "No such command" in capsys.readouterr().err
    assert captured == []
    assert captured_errors == []


def test_main_does_not_capture_invalid_option_parse_error(monkeypatch, capsys) -> None:
    captured: list[str] = []
    captured_errors: list[BaseException] = []
    monkeypatch.setattr(
        "app.cli.__main__.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        "app.cli.support.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured_errors.append(exc),
    )

    exit_code = main(["--definitely-wrong-option"])

    assert exit_code == 2
    assert "No such option: --definitely-wrong-option" in capsys.readouterr().err
    assert captured == []
    assert captured_errors == []


def test_main_captures_analytics_once_for_accepted_command(monkeypatch, capsys) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "app.cli.__main__.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    exit_code = main(["version"])

    assert exit_code == 0
    assert "opensre" in capsys.readouterr().out
    assert captured == ["install", "cli"]


def test_main_captures_command_metadata_for_version(monkeypatch, capsys) -> None:
    captured: list[dict[str, object] | None] = []
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked",
        lambda properties=None: captured.append(properties),
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    exit_code = main(["version"])

    assert exit_code == 0
    assert "opensre" in capsys.readouterr().out
    assert captured == [
        {
            "entrypoint": "opensre",
            "command_path": "opensre version",
            "command_family": "version",
            "json_output": False,
            "verbose": False,
            "debug": False,
            "yes": False,
            "interactive": True,
            "command_leaf": "version",
        }
    ]


def test_main_captures_command_metadata_for_remote_health(monkeypatch) -> None:
    captured: list[dict[str, object] | None] = []
    remote_module = importlib.import_module("app.cli.commands.remote")
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked",
        lambda properties=None: captured.append(properties),
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr(
        remote_module,
        "_load_remote_client",
        lambda *_args, **_kwargs: SimpleNamespace(base_url="http://example.test"),
    )
    monkeypatch.setattr(remote_module, "run_remote_health_check", lambda **_kwargs: None)

    exit_code = main(["remote", "--url", "http://example.test", "health"])

    assert exit_code == 0
    properties = captured[0]
    assert properties is not None
    assert properties["command_path"] == "opensre remote health"
    assert properties["command_family"] == "remote"
    assert properties["subcommand"] == "health"
    assert properties["command_leaf"] == "health"


def test_main_captures_command_metadata_for_nested_remote_ops(monkeypatch, capsys) -> None:
    captured: list[dict[str, object] | None] = []
    remote_module = importlib.import_module("app.cli.commands.remote")
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked",
        lambda properties=None: captured.append(properties),
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    status = SimpleNamespace(
        provider="railway",
        project="proj",
        service="svc",
        deployment_id="dep",
        deployment_status="success",
        environment="production",
        url="https://example.test",
        health="healthy",
        metadata={},
    )
    provider = SimpleNamespace(status=lambda _scope: status)
    scope = SimpleNamespace(provider="railway", project="proj", service="svc")
    monkeypatch.setattr(
        remote_module,
        "_resolve_remote_ops_scope",
        lambda _ctx: (provider, scope),
    )
    monkeypatch.setattr(remote_module, "_persist_remote_ops_scope", lambda _scope: None)

    exit_code = main(["remote", "ops", "status"])

    assert exit_code == 0
    assert "Provider: railway" in capsys.readouterr().out
    properties = captured[0]
    assert properties is not None
    assert properties["command_path"] == "opensre remote ops status"
    assert properties["command_family"] == "remote"
    assert properties["subcommand"] == "ops"
    assert properties["command_leaf"] == "status"


def test_main_emits_first_run_install_before_cli_invoked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    # This test validates analytics event ordering only; avoid real Sentry init
    # side effects (e.g. sdk integration hooks) that are unrelated to the
    # install/cli-invoked event contract.
    monkeypatch.setattr("app.cli.__main__.init_sentry", lambda **_kw: None)
    provider.shutdown_analytics(flush=False)
    provider._instance = None
    provider._cached_anonymous_id = None
    provider._cached_identity_persistence = "unknown"
    provider._first_run_marker_created_this_process = False
    provider._pending_user_id_load_failures.clear()
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "_event_log_state", provider._EventLogState())
    monkeypatch.setattr(provider.atexit, "register", lambda _func: None)
    posted_payloads = _stub_analytics_httpx(monkeypatch)

    exit_code = main(["version"])

    assert exit_code == 0
    assert "opensre" in capsys.readouterr().out
    assert [payload["json"]["event"] for payload in posted_payloads] == [
        Event.INSTALL_DETECTED.value,
        Event.CLI_INVOKED.value,
    ]
    provider.shutdown_analytics(flush=False)
    provider._instance = None


@pytest.mark.parametrize(
    ("argv", "subcommand_event", "setup"),
    [
        (
            ["onboard"],
            "onboard_started",
            "app.cli.wizard.run_wizard",
        ),
        (
            ["integrations", "list"],
            "integrations_listed",
            "app.integrations.cli.cmd_list",
        ),
        (
            ["tests", "list"],
            "tests_listed",
            "app.cli.tests.discover.load_test_catalog",
        ),
    ],
)
def test_main_captures_cli_invoked_before_reported_subcommand_families(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    subcommand_event: str,
    setup: str,
) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "app.cli.__main__.capture_first_run_if_needed", lambda: captured.append("install")
    )
    monkeypatch.setattr(
        "app.cli.__main__.capture_cli_invoked", lambda *_args: captured.append("cli")
    )
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    if setup == "app.cli.wizard.run_wizard":
        onboard_module = importlib.import_module("app.cli.commands.onboard")
        monkeypatch.setattr(setup, lambda: 0)
        monkeypatch.setattr(
            onboard_module,
            "capture_onboard_started",
            lambda: captured.append(subcommand_event),
        )
        monkeypatch.setattr(onboard_module, "capture_onboard_completed", lambda _cfg: None)
    elif setup == "app.integrations.cli.cmd_list":
        integrations_module = importlib.import_module("app.cli.commands.integrations")
        monkeypatch.setattr(setup, lambda: None)
        monkeypatch.setattr(
            integrations_module,
            "capture_integrations_listed",
            lambda: captured.append(subcommand_event),
        )
    else:
        tests_module = importlib.import_module("app.cli.commands.tests")
        monkeypatch.setattr(setup, _EmptyCatalog)

        def _capture_tests_listed(_category: str, *, search: bool) -> None:
            _ = (_category, search)
            captured.append(subcommand_event)

        monkeypatch.setattr(
            tests_module,
            "capture_tests_listed",
            _capture_tests_listed,
        )

    exit_code = main(argv)

    assert exit_code == 0
    assert captured[:3] == ["install", "cli", subcommand_event]


def test_no_interactive_falls_through_to_landing_page(monkeypatch) -> None:
    """Regression for Greptile P1 (PR #591): --no-interactive previously ran
    `raise SystemExit(run_repl(...))` unconditionally on a TTY, returning 0 but
    never reaching render_landing().  The fix guards the SystemExit on
    `config.enabled`, so disabled mode falls through to render_landing().
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)

    # Force the TTY branch so the regression path is actually exercised.
    monkeypatch.setattr("app.cli.__main__.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.cli.__main__.sys.stdout.isatty", lambda: True)

    # Force disabled interactive config via the loader.  Return a disabled config
    # regardless of how the CLI resolved the flag.
    monkeypatch.setattr(
        "app.cli.interactive_shell.config.ReplConfig.load",
        classmethod(lambda _cls, **_kw: ReplConfig(enabled=False, layout="classic")),
    )

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "app.cli.__main__.render_landing",
        lambda: landing_calls.append(1),
    )

    # run_repl must NOT be invoked when config.enabled is False.
    def _fail_if_called(**_kw: object) -> int:
        raise AssertionError("run_repl must not run when config.enabled=False")

    with patch("app.cli.interactive_shell.run_repl", side_effect=_fail_if_called):
        exit_code = main(["--no-interactive"])

    assert exit_code == 0
    assert landing_calls == [1], "render_landing should be called exactly once"


def test_default_no_args_enters_repl(monkeypatch) -> None:
    """Regression: the default invocation `opensre` (no args, TTY) must enter
    the REPL.  A previous Click misconfiguration (is_flag + flag_value=False)
    made the `interactive` kwarg resolve to False even with no flag, so every
    local run silently rendered the landing page.  Assert the CLI passes
    cli_enabled=True into ReplConfig.load and actually calls run_repl.
    """
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("app.cli.__main__.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.cli.__main__.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []
    orig_load = ReplConfig.load

    @classmethod  # type: ignore[misc]
    def spy_load(cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return orig_load(**kw)

    monkeypatch.setattr("app.cli.interactive_shell.config.ReplConfig.load", spy_load)

    landing_calls: list[int] = []
    monkeypatch.setattr(
        "app.cli.__main__.render_landing",
        lambda: landing_calls.append(1),
    )

    with (
        patch("app.cli.interactive_shell.run_repl", return_value=0),
        patch("app.cli.interactive_shell.loop.run_repl", return_value=0),
    ):
        exit_code = main([])

    assert exit_code == 0
    assert len(load_calls) == 1
    assert load_calls[0].get("cli_enabled") is True, (
        f"default no-args run must pass cli_enabled=True, got {load_calls[0]}"
    )
    assert load_calls[0].get("cli_reload") is None, (
        f"default no-args run must leave reload env/config overridable, got {load_calls[0]}"
    )
    assert landing_calls == [], "REPL should run, not landing page"


def test_no_reload_flag_passes_reload_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)
    monkeypatch.setattr("app.cli.__main__.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("app.cli.__main__.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.cli.__main__.sys.stdout.isatty", lambda: True)

    load_calls: list[dict] = []

    @classmethod  # type: ignore[misc]
    def spy_load(_cls, **kw):  # type: ignore[no-untyped-def]
        load_calls.append(kw)
        return ReplConfig(enabled=True, layout="classic", reload=False)

    monkeypatch.setattr("app.cli.interactive_shell.config.ReplConfig.load", spy_load)

    with (
        patch("app.cli.interactive_shell.run_repl", return_value=0),
        patch("app.cli.interactive_shell.loop.run_repl", return_value=0),
    ):
        exit_code = main(["--no-reload"])

    assert exit_code == 0
    assert load_calls == [
        {"cli_enabled": True, "cli_layout": None, "cli_reload": False},
    ]
