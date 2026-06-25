"""Tests for slash command dispatch."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest
from prompt_toolkit.history import FileHistory
from rich.console import Console

from app.cli.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.command_registry import repl_data as repl_data_module
from app.cli.interactive_shell.command_registry.investigation import (
    _validate_investigate_args,
    _validate_save_args,
)
from app.cli.interactive_shell.command_registry.tasks_cmds import _validate_cancel_args
from app.cli.interactive_shell.config.tool_catalog import ToolCatalogEntry
from app.cli.interactive_shell.runtime.background import BackgroundInvestigationRecord
from app.cli.interactive_shell.runtime.session import ReplSession
from app.cli.interactive_shell.runtime.tasks import TaskKind, TaskStatus


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


class TestDispatchSlash:
    def test_exit_returns_false(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert dispatch_slash("/exit", session, console) is False
        assert dispatch_slash("/quit", session, console) is False

    def test_help_lists_all_commands(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/help", session, console) is True
        output = buf.getvalue()
        for name in SLASH_COMMANDS:
            assert name in output
        assert "Use /help <command> for usage." in output
        assert "/model set <provider>" not in output

    def test_question_mark_shortcut_runs_help(self) -> None:
        """`/?` is the canonical shortcut for `/help` (vim / less convention)."""
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/?", session, console) is True
        output = buf.getvalue()
        # Any slash command name suffices as proof the help table rendered.
        assert "/help" in output
        assert "/tools" in output

    def test_help_command_detail_shows_usage(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/help /model", session, console) is True
        output = buf.getvalue()
        assert "Show or change active LLM settings." in output
        assert "/model set <provider>" in output
        assert "In a TTY, bare /model opens an interactive menu." in output

    def test_help_category_shows_compact_section(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/help tasks", session, console) is True
        output = buf.getvalue()
        assert "Tasks commands" in output
        assert "/tasks" in output
        assert "/cancel <task_id>" not in output

    def test_tty_help_dispatch_uses_interactive_picker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli.interactive_shell.command_registry import help as help_cmd

        session = ReplSession()
        console, buf = _capture()
        picker_called: list[bool] = []
        monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(
            help_cmd, "choose_help_command", lambda _sections: picker_called.append(True)
        )

        assert dispatch_slash("/help", session, console) is True

        assert picker_called == [True]
        assert buf.getvalue() == ""

    def test_bare_slash_previews_all_commands(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/", session, console) is True
        output = buf.getvalue()
        assert "Slash commands" in output
        assert "/help" in output
        assert "/tools" in output
        assert "unknown command" not in output

    def test_trust_toggle(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert session.trust_mode is False
        dispatch_slash("/trust", session, console)
        assert session.trust_mode is True
        dispatch_slash("/trust off", session, console)
        assert session.trust_mode is False

    def test_effort_sets_session_preference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            provider = "openai"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        session = ReplSession()
        console, buf = _capture()

        dispatch_slash("/effort max", session, console)

        assert session.reasoning_effort == "max"
        output = buf.getvalue()
        assert "reasoning effort set to" in output
        assert "runtime: xhigh" in output

    def test_effort_rejects_unknown_value(self) -> None:
        session = ReplSession()
        console, buf = _capture()

        dispatch_slash("/effort turbo", session, console)

        assert session.reasoning_effort is None
        assert "unknown reasoning effort" in buf.getvalue()

    def test_effort_shows_default_config_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4-7"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        session = ReplSession()
        console, buf = _capture()

        dispatch_slash("/effort", session, console)

        output = buf.getvalue()
        assert "reasoning effort:" in output
        assert "(default)" in output
        assert "default config:" in output
        assert "anthropic does not use reasoning-effort overrides" in output

    def test_new_clears_session(self) -> None:
        session = ReplSession()
        session.record("alert", "test")
        session.last_state = {"x": 1}
        session.trust_mode = True
        console, _ = _capture()

        dispatch_slash("/new", session, console)

        assert session.history == []
        assert session.last_state is None
        assert session.trust_mode is True  # /new keeps trust mode

    def test_status_shows_session_fields(self) -> None:
        session = ReplSession()
        session.record("alert", "hello")
        session.reasoning_effort = "max"
        console, buf = _capture()
        dispatch_slash("/status", session, console)
        output = buf.getvalue()
        assert "interactions" in output
        assert "reasoning effort" in output
        assert "trust mode" in output
        assert "grounding cli cache" in output
        assert "grounding docs cache" in output

    def test_background_toggle_and_status(self) -> None:
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/background on", session, console) is True
        assert session.background_mode_enabled is True

        assert dispatch_slash("/background status", session, console) is True
        output = buf.getvalue()
        assert "Background mode" in output
        assert "notify channels" in output
        assert "none" in output

    def test_background_list_empty_message(self) -> None:
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/background list", session, console) is True
        assert "no background investigations" in buf.getvalue().lower()

    def test_background_show_and_use_completed_record(self) -> None:
        session = ReplSession()
        session.background_investigations["bg123"] = BackgroundInvestigationRecord(
            task_id="bg123",
            status="completed",
            command="free-text investigation",
            root_cause="database connection pool exhausted",
            top_analysis=("rds cpu saturation",),
            next_steps=("scale the connection pool",),
            final_state={"root_cause": "database connection pool exhausted", "service": "api"},
        )
        console, buf = _capture()

        assert dispatch_slash("/background show bg123", session, console) is True
        assert "database connection pool exhausted" in buf.getvalue()

        assert dispatch_slash("/background use bg123", session, console) is True
        assert session.last_state == {
            "root_cause": "database connection pool exhausted",
            "service": "api",
        }
        assert session.accumulated_context["service"] == "api"

    def test_background_notify_set_rejects_invalid_channel(self) -> None:
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/background notify set pagerduty", session, console) is True
        output = buf.getvalue()
        assert "invalid channel" in output
        assert session.background_notification_preferences.channels == ()

    def test_background_notify_set_updates_channels(self) -> None:
        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/background notify set email", session, console)
        assert session.background_notification_preferences.channels == ("email",)
        assert "background notify channels set" in buf.getvalue().lower()

    def test_unknown_command_does_not_exit(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/made-up", session, console) is True
        assert "unknown command" in buf.getvalue()

    def test_unknown_command_suggests_close_match(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/modle", session, console) is True
        output = buf.getvalue()
        assert "unknown command" in output
        assert "Did you mean" in output
        assert "/model" in output

    def test_local_llm_is_not_a_builtin_slash_action(self) -> None:
        assert "/local-llm" not in SLASH_COMMANDS
        assert "/local_llm" not in SLASH_COMMANDS

    def test_hermes_slash_command_delegates_to_bare_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity

        calls: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str]) -> bool:
            calls.append(args)
            return True

        monkeypatch.setattr(cli_parity, "run_cli_command", _fake_run_cli_command)

        session = ReplSession()
        console, _ = _capture()

        assert dispatch_slash("/hermes", session, console) is True
        assert calls == [["hermes"]]

    def test_empty_input_is_noop(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert dispatch_slash("   ", session, console) is True

    def test_history_shows_persisted_prompt_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        history = FileHistory(str(tmp_path / "interactive_history"))
        history.store_string("opensre health")
        history.store_string("/integrations list")

        session = ReplSession()
        session.record("alert", "current session only")
        console, buf = _capture()

        assert dispatch_slash("/history", session, console) is True
        output = buf.getvalue()
        assert "Command history" in output
        assert "opensre health" in output
        assert "/integrations list" in output
        assert "current session only" not in output

    def test_investigate_file_read_failure_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_errors: list[BaseException] = []

        monkeypatch.setattr(Path, "exists", lambda _self: True)
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda _self, **_kwargs: (_ for _ in ()).throw(RuntimeError("read broke")),
        )
        monkeypatch.setattr(
            "app.cli.interactive_shell.error_handling.exception_reporting.capture_exception",
            lambda exc, **_kwargs: captured_errors.append(exc),
        )

        session = ReplSession()
        console, buf = _capture()

        assert dispatch_slash("/investigate incident.json", session, console) is True

        assert "cannot read file" in buf.getvalue()
        assert len(captured_errors) == 1
        assert isinstance(captured_errors[0], RuntimeError)

    def test_save_failure_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_errors: list[BaseException] = []

        monkeypatch.setattr(
            Path,
            "write_text",
            lambda _self, *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("write broke")),
        )
        monkeypatch.setattr(
            "app.cli.interactive_shell.error_handling.exception_reporting.capture_exception",
            lambda exc, **_kwargs: captured_errors.append(exc),
        )

        session = ReplSession()
        session.last_state = {"root_cause": "cache issue", "problem_md": "details"}
        console, buf = _capture()

        assert dispatch_slash("/save report.md", session, console) is True

        assert "save failed" in buf.getvalue()
        assert len(captured_errors) == 1
        assert isinstance(captured_errors[0], RuntimeError)


class TestSpecificListCommands:
    """Coverage for /integrations list, /mcp list, /model show, and /tools list."""

    _FAKE_INTEGRATIONS = [
        {"service": "datadog", "source": "store", "status": "ok", "detail": "API ok"},
        {"service": "slack", "source": "env", "status": "failed", "detail": "No bot token"},
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
        {"service": "openclaw", "source": "store", "status": "failed", "detail": "401 from server"},
    ]

    def _patch_verify(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE_INTEGRATIONS),
        )

    def test_integrations_list_includes_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", ReplSession(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "slack" in output
        assert "openclaw" in output
        assert "github" in output

    def test_mcp_list_shows_only_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp list", ReplSession(), console)
        output = buf.getvalue()
        assert "openclaw" in output
        assert "github" in output
        assert "datadog" not in output

    def _patch_llm(self, monkeypatch: object) -> None:
        """Provide a stable fake LLMSettings so the test doesn't depend on env."""

        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())

    def test_model_show_displays_provider_and_models(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model show", ReplSession(), console)
        output = buf.getvalue()
        assert "provider" in output
        assert "reasoning model" in output
        assert "toolcall model" in output
        assert "anthropic" in output

    def test_model_show_displays_ollama_model(self, monkeypatch: object) -> None:
        class _FakeLLM:
            provider = "ollama"
            ollama_model = "qwen2.5:7b"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        console, buf = _capture()
        dispatch_slash("/model show", ReplSession(), console)
        output = buf.getvalue()
        assert "ollama" in output
        assert "qwen2.5:7b" in output
        assert "default" not in output

    def test_model_show_handles_missing_env_gracefully(self, monkeypatch: object) -> None:
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: None)
        console, buf = _capture()
        dispatch_slash("/model show", ReplSession(), console)
        assert "LLM settings unavailable" in buf.getvalue()

    def test_integrations_list_empty_prints_onboarding_hint(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            list,  # callable returning []
        )
        console, buf = _capture()
        dispatch_slash("/integrations list", ReplSession(), console)
        assert "opensre onboard" in buf.getvalue()

    def test_tools_list_prints_registered_tools(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import tools_cmds as tools_cmd_module

        monkeypatch.setattr(
            tools_cmd_module,
            "build_tool_catalog",
            lambda: [
                ToolCatalogEntry(
                    name="search_github",
                    surfaces=("investigation", "chat"),
                    description="Search GitHub code.",
                    source_file="app/tools/search_github.py",
                    input_schema_summary="query: string",
                )
            ],
        )

        console, buf = _capture()
        dispatch_slash("/tools list", ReplSession(), console)
        output = buf.getvalue()
        assert "search_github" in output
        assert "investigation" in output
        assert "Search GitHub code." in output


# ---------------------------------------------------------------------------
# Task 3 — Click-shadowing commands
# ---------------------------------------------------------------------------


class TestIntegrationsCommand:
    _FAKE = [
        {"service": "datadog", "source": "env", "status": "ok", "detail": "ok"},
        {"service": "slack", "source": "env", "status": "missing", "detail": "no token"},
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
    ]

    def _patch(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE),
        )

    def test_list_shows_all_services_including_github(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", ReplSession(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "github" in output

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations", ReplSession(), console)
        assert "datadog" in buf.getvalue()

    def test_verify_reports_issues(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations verify", ReplSession(), console)
        assert "need attention" in buf.getvalue()

    def test_verify_all_ok(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: [
                {"service": "datadog", "source": "env", "status": "ok", "detail": "ok"},
            ],
        )
        console, buf = _capture()
        dispatch_slash("/integrations verify", ReplSession(), console)
        assert "all integrations ok" in buf.getvalue()

    def test_show_known_service(self, monkeypatch: object) -> None:
        verified: list[str | None] = []

        def _verify_one(service: str) -> dict[str, str]:
            verified.append(service)
            return {
                "service": service,
                "source": "env",
                "status": "ok",
                "detail": "ok",
            }

        monkeypatch.setattr(
            repl_data_module,
            "configured_integration_names",
            lambda: ["datadog"],
        )
        monkeypatch.setattr(repl_data_module, "verify_integration", _verify_one)
        console, buf = _capture()
        dispatch_slash("/integrations show datadog", ReplSession(), console)
        assert verified == ["datadog"]
        assert "datadog" in buf.getvalue()

    def test_show_unknown_service(self, monkeypatch: object) -> None:
        monkeypatch.setattr(repl_data_module, "configured_integration_names", lambda: ["datadog"])
        session = ReplSession()
        session.record("slash", "/integrations show bogus")
        console, buf = _capture()
        dispatch_slash("/integrations show bogus", session, console)
        assert "service not found" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_show_missing_arg(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show", ReplSession(), console)
        assert "usage" in buf.getvalue()

    def test_unknown_subcommand_prints_hint(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()

    def test_setup_delegates_to_cli(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(m, "run_cli_command", lambda _, args: (captured.append(args), True)[1])
        dispatch_slash("/integrations setup", ReplSession(), Console())
        assert captured == [["integrations", "setup"]]

    def test_remove_uses_native_store_removal(self, monkeypatch: object) -> None:
        import app.analytics.cli as analytics_cli
        import app.integrations.store as store
        from app.cli.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "yes")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        monkeypatch.setattr(analytics_cli, "capture_integration_removed", lambda *_: None)
        dispatch_slash("/integrations remove slack", ReplSession(), Console())
        assert removed == ["slack"]

    def test_remove_cancelled_does_not_touch_store(self, monkeypatch: object) -> None:
        import app.integrations.store as store
        from app.cli.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "no")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        dispatch_slash("/integrations remove slack", ReplSession(), Console())
        assert removed == []


class TestMcpCommand:
    _FAKE = [
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
        {"service": "openclaw", "source": "store", "status": "ok", "detail": "ok"},
    ]

    def _patch(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE),
        )

    def test_list_shows_mcp_services(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp list", ReplSession(), console)
        assert "github" in buf.getvalue()

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp", ReplSession(), console)
        assert "github" in buf.getvalue()

    def test_connect_delegates_to_cli(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(m, "run_cli_command", lambda _, args: (captured.append(args), True)[1])
        dispatch_slash("/mcp connect", ReplSession(), Console())
        assert captured == [["integrations", "setup"]]

    def test_disconnect_uses_native_store_removal(self, monkeypatch: object) -> None:
        import app.analytics.cli as analytics_cli
        import app.integrations.store as store
        from app.cli.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "yes")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        monkeypatch.setattr(analytics_cli, "capture_integration_removed", lambda *_: None)
        dispatch_slash("/mcp disconnect github", ReplSession(), Console())
        assert removed == ["github"]

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestModelCommand:
    def _patch_llm(self, monkeypatch: object) -> None:
        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())

    def test_show_displays_model_info(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model show", ReplSession(), console)
        assert "anthropic" in buf.getvalue()

    def test_show_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model", ReplSession(), console)
        assert "anthropic" in buf.getvalue()

    def test_model_interactive_set_flow_applies_selection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync
        from app.cli.interactive_shell.command_registry.model import command as model_cmd

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        selections = iter(["set", "anthropic", "__provider_default__"])
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(selections))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model", ReplSession(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "reasoning model:" in output
        assert "LLM_PROVIDER=anthropic" in env_path.read_text(encoding="utf-8")

    def test_model_interactive_show_then_done_shows_table_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_llm(monkeypatch)
        from app.cli.interactive_shell.command_registry.model import command as model_cmd

        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        picks = iter(["show", "done"])
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(picks))
        console, buf = _capture()
        dispatch_slash("/model", ReplSession(), console)
        assert "anthropic" in buf.getvalue()

    def test_model_interactive_escape_backs_out_without_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_llm(monkeypatch)
        from app.cli.interactive_shell.command_registry.model import command as model_cmd

        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        selections = iter(
            [
                "set",  # root -> set
                "anthropic",  # provider selected
                None,  # Esc from model selection -> back to provider list
                None,  # Esc from provider list -> back to root action list
                None,  # Esc at root -> close menu
            ]
        )
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(selections))
        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/model", session, console)

        assert "switched LLM provider" not in buf.getvalue()
        assert session.history[-1]["ok"] is True

    def test_set_switches_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync
        from app.services import llm_client

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        reset_calls: list[str] = []
        monkeypatch.setattr(llm_client, "reset_llm_singletons", lambda: reset_calls.append("reset"))
        # /model set now refuses to half-update .env when the target provider
        # has no usable credential; supply one so the happy path still runs.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic", ReplSession(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "anthropic" in output
        # Reviewer (#1192) couldn't tell from "anthropic (X)" which slot the
        # model went into; the success message must now explicitly label the
        # reasoning slot and name the env var it lands in.
        assert "reasoning model:" in output
        assert "ANTHROPIC_REASONING_MODEL" in output
        assert "LLM_PROVIDER=anthropic" in (tmp_path / ".env").read_text(encoding="utf-8")
        assert reset_calls == ["reset"]

    def test_set_refuses_when_credential_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Reviewer ask (#1192): if the target provider has no API key in env
        or keyring, /model set must NOT touch .env or os.environ — otherwise
        the user lands in a broken half-state where LLM_PROVIDER points at a
        provider with no usable credential and the next /model show prints
        'LLM settings unavailable'."""
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Keyring lookups in CI / sandboxes are flaky; force the helper into
        # the env-only path so the test is deterministic.
        monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
        # LLM_PROVIDER must not be rewritten by a rejected switch — capture
        # what it was before so we can assert it is unchanged.
        monkeypatch.setenv("LLM_PROVIDER", "gemini")

        console, buf = _capture()
        dispatch_slash("/model set anthropic", ReplSession(), console)

        output = buf.getvalue()
        assert "missing credential for anthropic" in output
        assert "ANTHROPIC_API_KEY" in output
        assert "switched LLM provider" not in output
        # No .env should have been written.
        assert not env_path.exists()
        # And the live LLM_PROVIDER must be untouched.
        import os

        assert os.environ.get("LLM_PROVIDER") == "gemini"

    def test_set_missing_provider_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/model set", ReplSession(), console)
        assert "usage" in buf.getvalue()

    def test_set_unknown_reasoning_model_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        session = ReplSession()
        session.record("slash", "/model set anthropic not-a-real-model-xyz")

        console, buf = _capture()
        dispatch_slash("/model set anthropic not-a-real-model-xyz", session, console)

        output = buf.getvalue()
        assert "unknown model for anthropic" in output
        assert "not-a-real-model-xyz" in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()
        assert session.history[-1]["ok"] is False

    def test_set_custom_reasoning_model_is_accepted_for_openai(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model set openai gpt-5.5", ReplSession(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "gpt-5.5" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=openai" in contents
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents

    def test_set_bare_model_updates_active_provider_reasoning_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        console, buf = _capture()
        dispatch_slash("/model set gpt-5.5", ReplSession(), console)

        output = buf.getvalue()
        assert "reasoning model set to" in output
        assert "gpt-5.5" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=" not in contents
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents

    def test_set_bare_gpt_words_normalizes_to_model_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        dispatch_slash("/model set gpt 5.5", ReplSession(), _capture()[0])

        contents = env_path.read_text(encoding="utf-8")
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents

    def test_switch_reasoning_model_normalizes_whitespace_slug(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Regression: the planner ``llm_set_provider`` tool dispatches the raw
        target straight to ``switch_reasoning_model`` (no CLI arg-splitting), so a
        spoken "set model to gpt 5.5" arrived as the single token ``"gpt 5.5"``.
        Because openai allows custom models, that malformed slug used to be
        persisted verbatim and then silently fail availability checks. It must be
        normalized to ``gpt-5.5`` instead."""
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync
        from app.cli.interactive_shell.command_registry import switch_reasoning_model

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        console, buf = _capture()
        ok = switch_reasoning_model("gpt 5.5", console)

        assert ok is True
        assert "gpt-5.5" in buf.getvalue()
        assert "gpt 5.5" not in buf.getvalue()
        contents = env_path.read_text(encoding="utf-8")
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents
        assert "gpt 5.5" not in contents

    def test_set_unknown_toolcall_model_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash(
            "/model set anthropic claude-opus-4-7 --toolcall-model not-a-real-model-xyz",
            ReplSession(),
            console,
        )

        output = buf.getvalue()
        assert "unknown model for anthropic" in output
        assert "not-a-real-model-xyz" in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()

    def test_set_with_toolcall_flag_writes_both_env_vars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """`/model set <provider> [model] --toolcall-model <m>` must persist both."""
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash(
            "/model set anthropic claude-opus-4-7 --toolcall-model claude-opus-4-7",
            ReplSession(),
            console,
        )

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "toolcall model" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in contents
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in contents
        assert "ANTHROPIC_TOOLCALL_MODEL=claude-opus-4-7" in contents

    def test_restore_resets_active_provider_to_default_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "not-a-real-model-xyz")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model restore", ReplSession(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "claude-opus-4-7" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in contents
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in contents
        assert "ANTHROPIC_MODEL=claude-opus-4-7" in contents

    def test_set_unknown_flag_prints_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic --made-up-flag x", ReplSession(), console)
        output = buf.getvalue()
        assert "unknown flag" in output
        assert "--made-up-flag" in output
        assert "usage" in output

    def test_set_toolcall_flag_without_value_prints_specific_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Reviewer ask: a missing flag value must say *which* flag, not just
        echo the generic usage line."""
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic --toolcall-model", ReplSession(), console)
        output = buf.getvalue()
        assert "missing value for --toolcall-model" in output
        # And we must not have written anything to .env on a parse failure.
        assert not env_path.exists() or "ANTHROPIC_TOOLCALL_MODEL" not in env_path.read_text()

    def test_toolcall_set_updates_only_toolcall_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """`/model toolcall set <m>` must persist only the toolcall env var."""
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync
        from app.services import llm_client

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        reset_calls: list[str] = []
        monkeypatch.setattr(llm_client, "reset_llm_singletons", lambda: reset_calls.append("reset"))
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        console, buf = _capture()
        dispatch_slash("/model toolcall set claude-opus-4-7", ReplSession(), console)

        output = buf.getvalue()
        assert "toolcall model set to" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_TOOLCALL_MODEL=claude-opus-4-7" in contents
        # Reasoning model is left untouched.
        assert "ANTHROPIC_REASONING_MODEL" not in contents
        # LLM_PROVIDER must not be rewritten by a toolcall-only switch.
        assert "LLM_PROVIDER=" not in contents
        assert reset_calls == ["reset"]

    def test_toolcall_set_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/model toolcall set", ReplSession(), console)
        assert "usage" in buf.getvalue()

    def test_toolcall_set_for_codex_provider_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Providers without a separate toolcall model (codex/claude-code/gemini-cli/ollama)
        must not silently accept toolcall overrides."""
        import app.cli.wizard.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("LLM_PROVIDER", "codex")
        console, buf = _capture()
        dispatch_slash("/model toolcall set gpt-5.4", ReplSession(), console)
        assert "does not expose a separate toolcall model" in buf.getvalue()

    def test_switch_alias_switches_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import app.cli.wizard.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model switch anthropic", ReplSession(), console)

        assert "switched LLM provider" in buf.getvalue()

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestVersionCommand:
    def test_shows_version_info(self) -> None:
        console, buf = _capture()
        dispatch_slash("/version", ReplSession(), console)
        output = buf.getvalue()
        assert "opensre" in output
        assert "python" in output
        assert "os" in output


class TestTemplateCommand:
    def test_known_template_prints_json(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template generic", ReplSession(), console)
        assert "alert_name" in buf.getvalue()

    def test_unknown_template_prints_hint(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template bogus", ReplSession(), console)
        assert "unknown template" in buf.getvalue()

    def test_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template", ReplSession(), console)
        assert "usage" in buf.getvalue()


class TestInvestigateFileCommand:
    def test_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/investigate", ReplSession(), console)
        assert "usage" in buf.getvalue()
        assert "/investigate <file|template>" in buf.getvalue()

    def test_missing_file_prints_error(self) -> None:
        session = ReplSession()
        session.record("slash", "/investigate /nonexistent/path.json")
        console, buf = _capture()
        dispatch_slash("/investigate /nonexistent/path.json", session, console)
        assert "file not found" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_valid_file_runs_investigation(self, tmp_path: object, monkeypatch: object) -> None:
        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        captured: list[str] = []

        def _fake(
            alert_text: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict:
            captured.append(alert_text)
            return {"root_cause": "test cause"}

        # Patch package re-export: slash handler does `from app.cli.investigation import ...`.
        monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _fake)
        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)
        assert session.last_state == {"root_cause": "test cause"}
        assert '{"alert_name": "test"}' in captured[0]

    def test_template_arg_runs_sample_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str] = []

        def _fake_sample(
            *,
            template_name: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict[str, str]:
            _ = (context_overrides, cancel_requested)
            captured.append(template_name)
            return {"root_cause": "sample cause"}

        monkeypatch.setattr("app.cli.investigation.run_sample_alert_for_session", _fake_sample)

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash("/investigate generic", session, console)

        assert captured == ["generic"]
        assert session.last_state == {"root_cause": "sample cause"}

    def test_template_arg_uses_background_launcher_when_mode_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launches: list[str] = []

        def _fake_start_background_template_investigation(
            *,
            template_name: str,
            session: ReplSession,
            console: Console,
            display_command: str,
        ) -> str:
            _ = (session, console, display_command)
            launches.append(template_name)
            return "bg123"

        monkeypatch.setattr(
            "app.cli.interactive_shell.command_registry.investigation.start_background_template_investigation",
            _fake_start_background_template_investigation,
        )

        session = ReplSession()
        session.background_mode_enabled = True
        console, _ = _capture()
        dispatch_slash("/investigate generic", session, console)

        assert launches == ["generic"]
        assert session.last_state is None

    def test_template_arg_tracks_cli_repl_file_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        track_calls: list[tuple[str, str, str | None]] = []

        class _TrackContext:
            def __enter__(self) -> None:
                return None

            def __exit__(self, exc_type, exc, tb) -> bool:
                _ = (exc_type, exc, tb)
                return False

        def _fake_track(*, entrypoint, trigger_mode, input_path=None, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            track_calls.append((entrypoint.value, trigger_mode.value, input_path))
            return _TrackContext()

        monkeypatch.setattr("app.analytics.cli.track_investigation", _fake_track)
        monkeypatch.setattr(
            "app.cli.investigation.run_sample_alert_for_session",
            lambda **_kwargs: {"root_cause": "sample cause"},
        )

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash("/investigate generic", session, console)

        assert track_calls == [("cli_repl_file", "file", "template:generic")]

    def test_template_name_takes_precedence_over_local_same_name_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "generic").write_text('{"alert_name": "local-file"}', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        calls: list[str] = []

        def _fake_sample(
            *,
            template_name: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict[str, str]:
            _ = (context_overrides, cancel_requested)
            calls.append(template_name)
            return {"root_cause": "template-wins"}

        monkeypatch.setattr("app.cli.investigation.run_sample_alert_for_session", _fake_sample)

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash("/investigate generic", session, console)

        assert calls == ["generic"]
        assert session.last_state == {"root_cause": "template-wins"}

    def test_missing_arg_in_tty_opens_interactive_menu(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli.interactive_shell.command_registry import investigation as investigation_cmd

        picks = iter(["generic"])
        captured: list[str] = []

        def _fake_sample(
            *,
            template_name: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict[str, str]:
            _ = (context_overrides, cancel_requested)
            captured.append(template_name)
            return {"root_cause": "sample from menu"}

        monkeypatch.setattr(investigation_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(investigation_cmd, "repl_choose_one", lambda **_: next(picks))
        monkeypatch.setattr("app.cli.investigation.run_sample_alert_for_session", _fake_sample)

        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/investigate", session, console)

        assert session.pending_prompt_default == "/investigate generic"
        assert session.pending_prompt_autosubmit is True
        assert captured == []

        dispatch_slash(session.take_pending_prompt_default(), session, console)
        assert session.take_pending_autosubmit() is True

        assert captured == ["generic"]
        assert session.last_state == {"root_cause": "sample from menu"}
        assert "usage" not in buf.getvalue().lower()

    def test_tty_investigate_menu_browse_path_runs_custom_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli.interactive_shell.command_registry import investigation as investigation_cmd

        alert_file = tmp_path / "custom_alert.json"
        alert_file.write_text('{"alert_name": "custom"}', encoding="utf-8")

        picks = iter(["__browse__"])
        captured: list[str] = []

        def _fake(
            alert_text: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict[str, str]:
            _ = (context_overrides, cancel_requested)
            captured.append(alert_text)
            return {"root_cause": "custom path run"}

        monkeypatch.setattr(investigation_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(investigation_cmd, "repl_choose_one", lambda **_: next(picks))
        monkeypatch.setattr(
            investigation_cmd,
            "_prompt_investigate_path",
            lambda _console: str(alert_file),
        )
        monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _fake)

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash("/investigate", session, console)

        assert session.take_pending_autosubmit() is True
        queued = session.take_pending_prompt_default()
        assert queued.startswith("/investigate ")
        assert captured == []

        dispatch_slash(queued, session, console)

        assert session.last_state == {"root_cause": "custom path run"}
        assert '"alert_name": "custom"' in captured[0]

    def test_investigate_file_tracks_cli_repl_file_source(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        track_calls: list[tuple[str, str]] = []

        class _TrackContext:
            def __enter__(self) -> None:
                return None

            def __exit__(self, exc_type, exc, tb) -> bool:
                _ = (exc_type, exc, tb)
                return False

        def _fake_track(*, entrypoint, trigger_mode, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            track_calls.append((entrypoint.value, trigger_mode.value))
            return _TrackContext()

        monkeypatch.setattr("app.analytics.cli.track_investigation", _fake_track)
        monkeypatch.setattr(
            "app.cli.investigation.run_investigation_for_session",
            lambda **_kwargs: {"root_cause": "test cause"},
        )
        session = ReplSession()
        console, _ = _capture()

        dispatch_slash(f"/investigate {alert_file}", session, console)

        assert track_calls == [("cli_repl_file", "file")]

    def test_investigate_accumulates_infra_context(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        """Regression for Greptile P1 (PR #591): /investigate previously skipped
        the context-accumulation step that free-text investigations perform, so
        subsequent follow-up alerts lost the infra hints (service / cluster /
        region) that /investigate just discovered."""

        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        def _fake(
            alert_text: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict:
            return {
                "root_cause": "disk full",
                "service": "orders-api",
                "cluster_name": "prod-us-east",
                "region": "us-east-1",
            }

        monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _fake)

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)

        # The next free-text alert must inherit these—proving accumulation ran.
        assert session.accumulated_context == {
            "service": "orders-api",
            "cluster_name": "prod-us-east",
            "region": "us-east-1",
        }

    def test_investigate_file_uses_background_launcher_when_mode_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alert_file = tmp_path / "alert.json"
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")
        launches: list[tuple[str, str]] = []

        def _fake_start_background_text_investigation(
            *,
            alert_text: str,
            session: ReplSession,
            console: Console,
            display_command: str,
        ) -> str:
            _ = (session, console)
            launches.append((alert_text, display_command))
            return "bg123"

        monkeypatch.setattr(
            "app.cli.interactive_shell.command_registry.investigation.start_background_text_investigation",
            _fake_start_background_text_investigation,
        )

        session = ReplSession()
        session.background_mode_enabled = True
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)

        assert len(launches) == 1
        assert '"alert_name": "test"' in launches[0][0]
        assert launches[0][1] == f"/investigate {alert_file}"
        assert session.last_state is None

    def test_investigate_opensre_error_marks_task_failed(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        from app.cli.interactive_shell.error_handling.errors import OpenSREError

        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        def _raise(
            alert_text: str,
            context_overrides: object = None,
            cancel_requested: object = None,
        ) -> dict[str, object]:
            raise OpenSREError("bad config")

        monkeypatch.setattr("app.cli.investigation.run_investigation_for_session", _raise)
        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)
        inv_tasks = [
            t for t in session.task_registry.list_recent(10) if t.kind == TaskKind.INVESTIGATION
        ]
        assert len(inv_tasks) == 1
        assert inv_tasks[0].status == TaskStatus.FAILED
        assert inv_tasks[0].error == "bad config"


# Task 4 — Session-state commands


class TestResumeCommand:
    """Tests for /resume command — session adoption and context restoration."""

    def test_apply_resume_adopts_target_session_and_restores_context(self, tmp_path: Path) -> None:
        """_apply_resume_data must flush the current session, adopt the target ID,
        reopen its file, and restore cli_agent_messages + accumulated_context."""
        import json
        from unittest.mock import patch

        from app.cli.interactive_shell.command_registry.session_cmds import _apply_resume_data
        from app.cli.interactive_shell.sessions.store import SessionStore

        session = ReplSession()
        old_id = session.session_id
        target_id = "old-abc-1234567890"

        with patch(
            "app.cli.interactive_shell.sessions.store._sessions_dir",
            return_value=tmp_path,
        ):
            SessionStore.open_session(session)
            session.record("chat", "pre-resume turn")

            # Pre-create a finalized target session file to resume into.
            target_path = tmp_path / f"{target_id}.jsonl"
            target_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session_start",
                                "session_id": target_id,
                                "started_at": "2026-05-29T10:00:00+00:00",
                            }
                        ),
                        json.dumps({"type": "turn", "kind": "chat", "text": "hello"}),
                        json.dumps(
                            {
                                "type": "conversation_snapshot",
                                "cli_agent_messages": [["user", "hello"], ["assistant", "hi"]],
                                "accumulated_context": {"service": "redis"},
                            }
                        ),
                        json.dumps({"type": "session_end", "total_turns": 1}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            data = SessionStore.load_session(target_id[:8])
            assert data is not None

            console, buf = _capture()
            slash_command = f"/resume {target_id[:8]}"
            result = _apply_resume_data(data, session, console, slash_command=slash_command)

            # Current (empty-ish) session file must be finalized without /resume turn
            old_records = [
                json.loads(line)
                for line in (tmp_path / f"{old_id}.jsonl").read_text().splitlines()
                if line.strip()
            ]
            assert old_records[-1]["type"] == "session_end"
            assert not any(r.get("kind") == "slash" for r in old_records if r.get("type") == "turn")

            # Target session is reopened — slash turn recorded on resumed session
            assert session.session_id == target_id
            target_records = [
                json.loads(line)
                for line in target_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert target_records[0]["type"] == "session_start"
            assert target_records[-1]["type"] == "turn"
            assert target_records[-1]["kind"] == "slash"
            assert target_records[-1]["text"].startswith("/resume")

        assert result is True
        assert session.session_id == target_id
        assert session.cli_agent_messages == [("user", "hello"), ("assistant", "hi")]
        assert session.accumulated_context == {"service": "redis"}
        output = buf.getvalue()
        assert "resumed session" in output
        assert "old-abc" in output

    def test_apply_resume_noop_when_no_messages_or_context(self) -> None:
        """When the session has no conversation, _apply_resume_data must return
        early without rotating the session."""
        from app.cli.interactive_shell.command_registry.session_cmds import _apply_resume_data

        session = ReplSession()
        old_id = session.session_id

        data: dict = {
            "session_id": "empty-sid",
            "name": "",
            "cli_agent_messages": [],
            "accumulated_context": {},
            "history": [],
            "turn_details": [],
            "has_snapshot": False,
        }
        console, buf = _capture()
        _apply_resume_data(data, session, console)

        assert session.session_id == old_id
        assert "no conversation to resume" in buf.getvalue()

    def test_apply_resume_displays_history_in_repl_format(self, tmp_path: Path) -> None:
        """History display uses REPL turn order and includes slash commands."""
        from unittest.mock import patch

        from app.cli.interactive_shell.command_registry.session_cmds import _apply_resume_data
        from app.cli.interactive_shell.sessions.store import SessionStore

        data = {
            "session_id": "display-test-abc123456789",
            "name": "My Session",
            "cli_agent_messages": [
                ("user", "what is opensre?"),
                ("assistant", "OpenSRE is a tool"),
            ],
            "accumulated_context": {},
            "history": [
                {"type": "turn", "kind": "slash", "text": "/status"},
                {"type": "turn", "kind": "chat", "text": "what is opensre?"},
            ],
            "turn_details": [],
            "has_snapshot": True,
        }
        session = ReplSession()
        console, buf = _capture()

        with patch(
            "app.cli.interactive_shell.sessions.store._sessions_dir",
            return_value=tmp_path,
        ):
            SessionStore.open_session(session)
            _apply_resume_data(data, session, console)

        output = buf.getvalue()
        assert "❯" in output
        assert "assistant" in output
        assert "$ /status" in output
        assert "you  " not in output
        assert "sre  " not in output
        assert "what is opensre?" in output
        assert "OpenSRE is a tool" in output

    def test_apply_resume_no_history_keeps_user_assistant_pairs_with_duplicate_prompts(
        self,
    ) -> None:
        """No-history rendering should not emit orphaned assistant blocks."""
        from app.cli.interactive_shell.command_registry.session_cmds import _apply_resume_data

        data = {
            "session_id": "display-no-history-abc123",
            "name": "No History",
            "cli_agent_messages": [
                ("user", "repeat"),
                ("assistant", "first answer"),
                ("user", "repeat"),
                ("assistant", "second answer"),
            ],
            "accumulated_context": {},
            "history": [],
            "turn_details": [],
            "has_snapshot": True,
        }

        session = ReplSession()
        console, buf = _capture()
        _apply_resume_data(data, session, console)

        output = buf.getvalue()
        assert output.count("❯ repeat") == 2
        assert output.count("assistant") == 2
        assert "first answer" in output
        assert "second answer" in output

    def test_planner_llm_error_persisted_to_cli_agent_messages(self) -> None:
        """PlannerLLMError must be added to cli_agent_messages so /resume can show it."""
        from unittest.mock import patch

        from app.cli.interactive_shell.routing.handle_message_with_agent.errors import (
            PlannerLLMError,
        )
        from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions import (
            execute_cli_actions,
        )

        session = ReplSession()
        console, _ = _capture()

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise PlannerLLMError("codex: quota or rate limit exceeded (exit 1)")

        with patch(
            "app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.agent_actions._plan_actions",
            side_effect=_raise,
        ):
            execute_cli_actions("check cpu usage", session, console)

        # The error turn must be recorded in cli_agent_messages for /resume
        assert len(session.cli_agent_messages) == 2
        assert session.cli_agent_messages[0] == ("user", "check cpu usage")
        assert session.cli_agent_messages[1][0] == "assistant"
        assert "quota" in session.cli_agent_messages[1][1]


class TestHistoryCommand:
    def test_empty_history_says_so(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        console, buf = _capture()
        dispatch_slash("/history", ReplSession(), console)
        assert "no history" in buf.getvalue()

    def test_history_shows_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        history = FileHistory(str(tmp_path / "interactive_history"))
        history.store_string("pod crash in prod")
        history.store_string("/status")

        console, buf = _capture()
        dispatch_slash("/history", ReplSession(), console)
        output = buf.getvalue()
        assert "Command history" in output
        assert "pod crash in prod" in output
        assert "/status" in output

    def test_history_ignores_session_only_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        session = ReplSession()
        session.record("alert", "bad input", ok=False)
        console, buf = _capture()
        dispatch_slash("/history", session, console)
        output = buf.getvalue()
        assert "no history" in output
        assert "bad input" not in output


class TestLastCommand:
    def test_no_investigation_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/last", ReplSession(), console)
        assert "no investigation" in buf.getvalue()

    def test_shows_root_cause(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "OOMKilled in orders-api"}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "OOMKilled in orders-api" in buf.getvalue()

    def test_shows_problem_md_when_no_root_cause(self) -> None:
        session = ReplSession()
        session.last_state = {"problem_md": "## Summary\n\nlatency spike"}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "latency spike" in buf.getvalue()

    def test_empty_state_says_no_content(self) -> None:
        session = ReplSession()
        session.last_state = {}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "no report content" in buf.getvalue()


class TestSaveCommand:
    def test_no_investigation_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/save out.md", ReplSession(), console)
        assert "nothing to save" in buf.getvalue()

    def test_missing_arg_prints_usage(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "x"}
        console, buf = _capture()
        dispatch_slash("/save", session, console)
        assert "usage" in buf.getvalue()

    def test_saves_markdown(self, tmp_path: object) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "db timeout", "problem_md": "## Details\n\nlatency"}
        dest = tmp_path / "report.md"  # type: ignore[operator]
        console, buf = _capture()
        dispatch_slash(f"/save {dest}", session, console)
        assert "saved" in buf.getvalue()
        content = dest.read_text()  # type: ignore[union-attr]
        assert "db timeout" in content

    def test_saves_json(self, tmp_path: object) -> None:
        import json

        session = ReplSession()
        session.last_state = {"root_cause": "db timeout"}
        dest = tmp_path / "report.json"  # type: ignore[operator]
        console, _ = _capture()
        dispatch_slash(f"/save {dest}", session, console)
        data = json.loads(dest.read_text())  # type: ignore[union-attr]
        assert data["root_cause"] == "db timeout"


class TestContextCommand:
    def test_empty_context_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/context", ReplSession(), console)
        assert "no infra context" in buf.getvalue()

    def test_shows_accumulated_keys(self) -> None:
        session = ReplSession()
        session.accumulated_context = {"service": "orders-api", "region": "us-east-1"}
        console, buf = _capture()
        dispatch_slash("/context", session, console)
        output = buf.getvalue()
        assert "orders-api" in output
        assert "us-east-1" in output


class TestCostCommand:
    def test_no_token_data_shows_placeholder(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cost", ReplSession(), console)
        assert "no LLM usage recorded yet" in buf.getvalue()

    def test_shows_token_counts_when_available(self) -> None:
        session = ReplSession()
        session.token_usage = {"input": 1000, "output": 500}
        session.llm_call_count = 2
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "1,000" in output
        assert "500" in output
        assert "llm calls" in output
        assert "2" in output

    def test_shows_estimate_labels_when_mixed(self) -> None:
        session = ReplSession()
        session.token_usage = {
            "input": 400,
            "output": 60,
            "input_measured": 300,
            "output_measured": 40,
            "input_estimated": 100,
            "output_estimated": 20,
        }
        session.llm_call_count = 2
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "provider + 100 est." in output
        assert "provider + 20 est." in output
        assert "includes estimates" in output


class TestVerboseCommand:
    def test_on_sets_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose on", ReplSession(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"
        assert "verbose logging on" in buf.getvalue()

    def test_off_removes_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.setenv("TRACER_VERBOSE", "1")  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose off", ReplSession(), console)
        assert "TRACER_VERBOSE" not in os.environ
        assert "verbose logging off" in buf.getvalue()

    def test_no_arg_turns_on(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, _ = _capture()
        dispatch_slash("/verbose", ReplSession(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"


class TestCompactCommand:
    def test_nothing_to_compact_when_small(self) -> None:
        session = ReplSession()
        for i in range(5):
            session.record("slash", f"/cmd{i}")
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        assert "nothing to compact" in buf.getvalue()
        assert len(session.history) == 6
        assert session.history[-1]["text"] == "/compact"

    def test_trims_to_20_when_over_limit(self) -> None:
        session = ReplSession()
        for i in range(30):
            session.record("slash", f"/cmd{i}")
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        assert len(session.history) == 20
        assert "compacted" in buf.getvalue()


class TestCancelCommand:
    def test_usage_without_task_id(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cancel", ReplSession(), console)
        assert "usage" in buf.getvalue().lower()
        assert "/tasks" in buf.getvalue()


class TestPrePolicyValidation:
    """Regression for #1712: ``validate_args`` runs before the policy gate, so
    invalid args never trigger the ``Proceed?`` confirmation prompt."""

    @pytest.mark.parametrize(
        "command,expected_usage_fragment",
        [
            ("/investigate", "/investigate <file|template>"),
            ("/save", "/save <path>"),
            ("/cancel", "/cancel <task_id>"),
        ],
    )
    def test_missing_arg_skips_policy_prompt(
        self, command: str, expected_usage_fragment: str
    ) -> None:
        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "n"

        session = ReplSession()

        console, buf = _capture()
        dispatch_slash(command, session, console, confirm_fn=_confirm, is_tty=True)

        assert expected_usage_fragment in buf.getvalue()
        assert confirm_calls == [], f"confirm_fn must not be called for {command} with no args"
        assert session.history[-1] == {"type": "slash", "text": command, "ok": False}

    def test_validate_args_fires_in_trust_mode(self) -> None:
        """Trust mode bypasses the policy prompt but must not bypass arg validation."""
        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "y"

        session = ReplSession()
        session.trust_mode = True

        console, buf = _capture()
        dispatch_slash("/investigate", session, console, confirm_fn=_confirm, is_tty=True)

        assert "/investigate <file|template>" in buf.getvalue()
        assert confirm_calls == [], "trust mode must not skip arg validation"

    def test_investigate_with_valid_arg_skips_policy_prompt(self, tmp_path: Path) -> None:
        """RCA from a file is the primary REPL action — no Proceed? gate."""
        alert_file = tmp_path / "alert.json"
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")

        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "n"

        session = ReplSession()
        console, buf = _capture()
        dispatch_slash(
            f"/investigate {alert_file}",
            session,
            console,
            confirm_fn=_confirm,
            is_tty=True,
        )

        assert confirm_calls == []
        assert "Proceed?" not in buf.getvalue()


class TestSlashValidatorFunctions:
    """Direct unit tests for the per-command pre-policy validators."""

    @pytest.mark.parametrize(
        "validator,expected_usage_fragment",
        [
            (_validate_investigate_args, "/investigate <file|template>"),
            (_validate_save_args, "/save <path>"),
            (_validate_cancel_args, "/cancel <task_id>"),
        ],
    )
    def test_returns_usage_when_args_empty(
        self, validator: object, expected_usage_fragment: str
    ) -> None:
        result = validator([])  # type: ignore[operator]
        assert isinstance(result, str)
        assert expected_usage_fragment in result

    @pytest.mark.parametrize(
        "validator,args",
        [
            (_validate_investigate_args, ["alert.json"]),
            (_validate_save_args, ["report.md"]),
            (_validate_cancel_args, ["task-abc"]),
        ],
    )
    def test_returns_none_when_args_present(self, validator: object, args: list[str]) -> None:
        assert validator(args) is None  # type: ignore[operator]


class TestRunCliCommand:
    """Regression: captured subprocess output must survive REPL prompt redraw."""

    def test_timed_delegate_replays_stdout_through_console(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout, text, encoding, errors
            assert capture_output is True
            assert cmd[:3] == [sys.executable, "-m", "app.cli"]
            assert cmd[3:] == ["update"]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="  opensre 1.0.0 is already up to date.\n",
                stderr="",
            )

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, buf = _capture()
        assert m.run_cli_command(console, ["update"], subprocess_timeout=30.0) is True
        assert "already up to date" in buf.getvalue()

    def test_config_delegate_captures_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout, text, encoding, errors
            assert capture_output is True
            assert cmd[:3] == [sys.executable, "-m", "app.cli"]
            assert cmd[3:] == ["config", "show"]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="Provider : cursor\n",
                stderr="",
            )

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, buf = _capture()
        assert m._cmd_config(ReplSession(), console, ["show"]) is True
        assert "Provider : cursor" in buf.getvalue()

    def test_capture_output_replays_stdout_through_console_without_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``capture_output=True`` must route child stdout through ``console`` even
        when no timeout is set, so non-interactive slash commands like ``/tests
        list`` do not lose their output to the parent stdout FD.
        """
        from app.cli.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
        ) -> subprocess.CompletedProcess[str]:
            del check, text, encoding, errors
            assert capture_output is True
            assert timeout is None
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="catalog row one\ncatalog row two\n",
                stderr="",
            )

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, buf = _capture()
        assert m.run_cli_command(console, ["tests", "list"], capture_output=True) is True
        assert "catalog row one" in buf.getvalue()
        assert "catalog row two" in buf.getvalue()

    def test_timeout_replays_decoded_partial_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        replayed: list[tuple[str, str | None]] = []

        def _fake_print_command_output(
            _console: Console,
            output: str,
            *,
            style: str | None = None,
        ) -> None:
            replayed.append((output, style))

        def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(
                cmd=[sys.executable, "-m", "app.cli", "update"],
                timeout=30.0,
                output=b"partial stdout\n",
                stderr=b"partial stderr\n",
            )

        monkeypatch.setattr(m, "print_command_output", _fake_print_command_output)
        monkeypatch.setattr(m.subprocess, "run", _fake_run)

        console, buf = _capture()
        assert m.run_cli_command(console, ["update"], subprocess_timeout=30.0) is True
        assert replayed == [("partial stdout\n", None), ("partial stderr\n", m.ERROR)]
        assert "timed out" in buf.getvalue()


class TestCliDelegatedCommands:
    """Coverage for commands that simply delegate to the underlying Click CLI."""

    @pytest.mark.parametrize(
        "command,expected_args",
        [
            ("/config show", ["config", "show"]),
            ("/remote health", ["remote", "health"]),
            ("/tests list", ["tests", "list"]),
            ("/guardrails audit", ["guardrails", "audit"]),
            ("/update", ["update"]),
            ("/uninstall", ["uninstall"]),
        ],
    )
    def test_command_delegation(
        self, monkeypatch: object, command: str, expected_args: list[str]
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        dispatch_slash(command, ReplSession(), Console())
        assert captured == [expected_args]

    def test_slash_onboard_delegates_to_run_cli_command(self, monkeypatch: object) -> None:
        """``/onboard`` must delegate to ``run_cli_command`` so the wizard runs
        with inherited stdin. The REPL loop guarantees exclusive stdin for
        ``/onboard`` via ``_WAIT_FOR_COMPLETION_COMMANDS`` in dispatch.py, so
        the wizard's prompt_toolkit Application no longer conflicts with the
        shell's active one.
        """
        from app.cli.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)

        session = ReplSession()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        dispatch_slash("/onboard", session, console)

        assert captured == [["onboard"]], "run_cli_command must be called with onboard args"

    @pytest.mark.parametrize("slash_input", ["/tests list", "/tests --help"])
    def test_slash_tests_subcommand_opts_into_output_capture(
        self, monkeypatch: object, slash_input: str
    ) -> None:
        """Both the known-subcommand fall-through (e.g. ``/tests list``) and
        the flag-style branch (e.g. ``/tests --help``) must call
        ``run_cli_command`` with ``capture_output=True`` so the delegated CLI
        output is replayed through the REPL console instead of vanishing onto
        the parent process's stdout FD.
        """
        from app.cli.interactive_shell.command_registry import cli_parity as m

        captured_kwargs: list[dict[str, object]] = []

        def _fake_run_cli_command(_console: Console, _args: list[str], **kwargs: object) -> bool:
            captured_kwargs.append(kwargs)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        dispatch_slash(slash_input, ReplSession(), Console())

        assert captured_kwargs == [{"capture_output": True}]

    @pytest.mark.parametrize(
        "slash_input",
        ["/guardrails", "/guardrails rules", "/guardrails --help"],
    )
    def test_slash_guardrails_opts_into_output_capture(
        self, monkeypatch: object, slash_input: str
    ) -> None:
        """Bare ``/guardrails`` (no subcommand), known subcommands, and flag-style
        invocations must all call ``run_cli_command`` with
        ``capture_output=True``. Without this, Click's usage block (printed for
        the no-subcommand case) and subcommand output bypass ``console.print``
        and never reach the REPL buffer — see issue #2388.
        """
        from app.cli.interactive_shell.command_registry import cli_parity as m

        captured_kwargs: list[dict[str, object]] = []

        def _fake_run_cli_command(_console: Console, _args: list[str], **kwargs: object) -> bool:
            captured_kwargs.append(kwargs)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        dispatch_slash(slash_input, ReplSession(), Console())

        assert captured_kwargs == [{"capture_output": True}]

    def test_slash_onboard_with_args_forwards_them_to_subprocess(self, monkeypatch: object) -> None:
        """Args passed to ``/onboard`` must be forwarded to the subprocess."""
        from app.cli.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)

        session = ReplSession()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        dispatch_slash("/onboard local_llm", session, console)

        assert captured == [["onboard", "local_llm"]]

    def test_tests_run_subcommand_starts_background_task(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        started: list[tuple[str, list[str], TaskKind, bool]] = []

        def _fake_start_background_cli_task(
            *,
            display_command: str,
            argv_list: list[str],
            session: ReplSession,
            console: Console,
            timeout_seconds: int,
            kind: TaskKind,
            use_pty: bool,
        ) -> object:
            del session, console, timeout_seconds
            started.append((display_command, argv_list, kind, use_pty))
            return object()

        monkeypatch.setattr(m, "start_background_cli_task", _fake_start_background_cli_task)
        dispatch_slash("/tests synthetic --scenario 001-replication-lag", ReplSession(), Console())

        assert started == [
            (
                "opensre tests synthetic --scenario 001-replication-lag",
                [
                    sys.executable,
                    "-m",
                    "app.cli",
                    "tests",
                    "synthetic",
                    "--scenario",
                    "001-replication-lag",
                ],
                TaskKind.SYNTHETIC_TEST,
                True,
            )
        ]

    def test_tests_picker_closes_selection_file_before_subprocess(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        selection_path = tmp_path / "selection.json"

        class _SelectionFile:
            name = str(selection_path)
            closed = False

            def __init__(self) -> None:
                selection_path.touch()

            def close(self) -> None:
                self.closed = True

        handle = _SelectionFile()
        started: list[str] = []

        def _fake_run(_command: list[str], **kwargs: object) -> object:
            assert handle.closed is True
            env = kwargs["env"]
            assert isinstance(env, dict)
            selection_path.write_text(
                '[{"command": ["opensre", "tests", "synthetic"], '
                '"command_display": "opensre tests synthetic"}]',
                encoding="utf-8",
            )

            class _Result:
                returncode = 0

            return _Result()

        monkeypatch.setattr(m.tempfile, "NamedTemporaryFile", lambda **_kwargs: handle)
        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            m,
            "start_background_cli_task",
            lambda **kwargs: started.append(kwargs["display_command"]),
        )

        dispatch_slash("/tests", ReplSession(), Console())

        assert started == ["opensre tests synthetic"]
        assert not selection_path.exists()

    def test_tests_flag_first_invocation_delegates_to_cli(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        delegated: list[list[str]] = []
        monkeypatch.setattr(
            m,
            "run_cli_command",
            lambda _console, args, **_kwargs: (delegated.append(args), True)[1],
        )

        dispatch_slash("/tests --help", ReplSession(), Console())

        assert delegated == [["tests", "--help"]]

    def test_tests_subcommand_typo_suggests_synthetic(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import cli_parity as m

        delegated: list[list[str]] = []
        started: list[list[str]] = []

        monkeypatch.setattr(
            m,
            "run_cli_command",
            lambda _console, args, **_kwargs: (delegated.append(args), True)[1],
        )
        monkeypatch.setattr(
            m,
            "start_background_cli_task",
            lambda **kwargs: started.append(kwargs["argv_list"]),
        )

        session = ReplSession()
        console, buf = _capture()
        dispatch_slash("/tests synthetics", session, console)

        output = buf.getvalue()
        assert "unknown tests subcommand" in output
        assert "Did you mean" in output
        assert "/tests synthetic" in output
        assert session.history[-1]["ok"] is False
        assert delegated == []
        assert started == []
