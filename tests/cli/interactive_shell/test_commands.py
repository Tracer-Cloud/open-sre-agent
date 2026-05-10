"""Tests for slash command dispatch."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from prompt_toolkit.history import FileHistory
from rich.console import Console

from app.cli.interactive_shell import command_registry as registry_module
from app.cli.interactive_shell.command_registry import repl_data as repl_data_module
from app.cli.interactive_shell.command_registry import types as command_types
from app.cli.interactive_shell.command_registry.investigation import (
    _validate_investigate_args,
    _validate_save_args,
)
from app.cli.interactive_shell.command_registry.tasks_cmds import _validate_cancel_args
from app.cli.interactive_shell.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.interactive_shell.session import ReplSession
from app.cli.interactive_shell.tasks import TaskKind, TaskStatus


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

    def test_question_mark_shortcut_runs_help(self) -> None:
        """`/?` is the canonical shortcut for `/help` (vim / less convention)."""
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/?", session, console) is True
        output = buf.getvalue()
        # Any slash command name suffices as proof the help table rendered.
        assert "/help" in output
        assert "/list" in output

    def test_bare_slash_previews_all_commands(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/", session, console) is True
        output = buf.getvalue()
        assert "Slash commands" in output
        assert "/help" in output
        assert "/list" in output
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

    def test_reset_clears_session(self) -> None:
        session = ReplSession()
        session.record("alert", "test")
        session.last_state = {"x": 1}
        session.trust_mode = True
        console, _ = _capture()

        dispatch_slash("/reset", session, console)

        assert session.history == []
        assert session.last_state is None
        assert session.trust_mode is True  # reset keeps trust mode

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

    def test_slash_commands_proxy_reads_current_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        command = command_types.SlashCommand("/hot", "hot reload test", lambda *_args: True)
        monkeypatch.setattr(registry_module, "SLASH_COMMANDS", {"/hot": command})

        assert SLASH_COMMANDS.get("/hot") is command
        assert list(SLASH_COMMANDS) == ["/hot"]

    def test_dispatch_slash_proxy_calls_current_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def _fake_dispatch(command_line: str, *_args: object, **_kwargs: object) -> bool:
            calls.append(command_line)
            return False

        monkeypatch.setattr(registry_module, "dispatch_slash", _fake_dispatch)

        assert dispatch_slash("/hot", ReplSession(), _capture()[0]) is False
        assert calls == ["/hot"]

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
        history.store_string("/list integrations")

        session = ReplSession()
        session.record("alert", "current session only")
        console, buf = _capture()

        assert dispatch_slash("/history", session, console) is True
        output = buf.getvalue()
        assert "Command history" in output
        assert "opensre health" in output
        assert "/list integrations" in output
        assert "current session only" not in output


class TestListCommand:
    """Coverage for /list integrations / models / mcp and the default summary."""

    _FAKE_INTEGRATIONS = [
        {"service": "datadog", "source": "store", "status": "ok", "detail": "API ok"},
        # `missing` integrations are omitted from `/list integrations`; keep slack visible here.
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

    def test_list_integrations_excludes_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list integrations", ReplSession(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "slack" in output
        # MCP-classified services are reserved for /list mcp.
        assert "openclaw" not in output
        assert "github" not in output

    def test_list_mcp_shows_only_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list mcp", ReplSession(), console)
        output = buf.getvalue()
        assert "openclaw" in output
        assert "github" in output
        assert "datadog" not in output

    def test_list_mcps_alias(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list mcps", ReplSession(), console)
        assert "openclaw" in buf.getvalue()

    def _patch_llm(self, monkeypatch: object) -> None:
        """Provide a stable fake LLMSettings so the test doesn't depend on env."""

        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())

    def test_list_models_shows_provider_and_models(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list models", ReplSession(), console)
        output = buf.getvalue()
        assert "provider" in output
        assert "reasoning model" in output
        assert "toolcall model" in output
        assert "anthropic" in output

    def test_list_models_shows_ollama_model(self, monkeypatch: object) -> None:
        class _FakeLLM:
            provider = "ollama"
            ollama_model = "qwen2.5:7b"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        console, buf = _capture()
        dispatch_slash("/list models", ReplSession(), console)
        output = buf.getvalue()
        assert "ollama" in output
        assert "qwen2.5:7b" in output
        assert "default" not in output

    def test_list_models_handles_missing_env_gracefully(self, monkeypatch: object) -> None:
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: None)
        console, buf = _capture()
        dispatch_slash("/list models", ReplSession(), console)
        assert "LLM settings unavailable" in buf.getvalue()

    def test_list_default_shows_all_three_sections(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list", ReplSession(), console)
        output = buf.getvalue()
        assert "Integrations" in output
        assert "MCP servers" in output
        assert "LLM connection" in output

    def test_list_unknown_target_prints_hint(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list bogus", ReplSession(), console)
        output = buf.getvalue()
        assert "unknown list target" in output
        assert "/list integrations" in output

    def test_list_empty_integrations_prints_onboarding_hint(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            list,  # callable returning []
        )
        console, buf = _capture()
        dispatch_slash("/list integrations", ReplSession(), console)
        assert "opensre onboard" in buf.getvalue()


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

    def test_list_shows_non_mcp_services(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", ReplSession(), console)
        assert "datadog" in buf.getvalue()
        assert "github" not in buf.getvalue()

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
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show datadog", ReplSession(), console)
        assert "datadog" in buf.getvalue()

    def test_show_unknown_service(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
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

    def test_remove_delegates_to_cli(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(m, "run_cli_command", lambda _, args: (captured.append(args), True)[1])
        dispatch_slash("/integrations remove slack", ReplSession(), Console())
        assert captured == [["integrations", "remove", "slack"]]


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

    def test_disconnect_delegates_to_cli(self, monkeypatch: object) -> None:
        from app.cli.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(m, "run_cli_command", lambda _, args: (captured.append(args), True)[1])
        dispatch_slash("/mcp disconnect github", ReplSession(), Console())
        assert captured == [["integrations", "remove", "github"]]

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
        from app.cli.interactive_shell.command_registry import model as model_cmd

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
        from app.cli.interactive_shell.command_registry import model as model_cmd

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
        from app.cli.interactive_shell.command_registry import model as model_cmd

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

    def test_set_unknown_reasoning_model_is_rejected_for_openai(
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
        dispatch_slash("/model set openai not-a-real-model-xyz", ReplSession(), console)

        output = buf.getvalue()
        assert "unknown model for openai" in output
        assert "not-a-real-model-xyz" in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()

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

    def test_investigate_accumulates_infra_context(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        """Regression for Greptile P1 (PR #591): /investigate previously skipped
        the context-accumulation step that `loop._run_new_alert` does after a
        free-text investigation, so subsequent follow-up alerts lost the infra
        hints (service / cluster / region) that /investigate just discovered."""

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

    def test_investigate_opensre_error_marks_task_failed(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        from app.cli.support.errors import OpenSREError

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
        assert "not available" in buf.getvalue()

    def test_shows_token_counts_when_available(self) -> None:
        session = ReplSession()
        session.token_usage = {"input": 1000, "output": 500}
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "1,000" in output
        assert "500" in output


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
            ("/investigate", "/investigate <file>"),
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

        assert "/investigate <file>" in buf.getvalue()
        assert confirm_calls == [], "trust mode must not skip arg validation"

    def test_valid_arg_still_fires_policy_prompt(self, tmp_path: Path) -> None:
        """The fix must not accidentally remove the policy gate entirely."""
        alert_file = tmp_path / "alert.json"
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")

        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "n"  # decline so we don't run a real investigation

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(
            f"/investigate {alert_file}",
            session,
            console,
            confirm_fn=_confirm,
            is_tty=True,
        )

        assert len(confirm_calls) == 1, "policy prompt must still fire for valid args"


class TestSlashValidatorFunctions:
    """Direct unit tests for the per-command pre-policy validators."""

    @pytest.mark.parametrize(
        "validator,expected_usage_fragment",
        [
            (_validate_investigate_args, "/investigate <file>"),
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


class TestCliDelegatedCommands:
    """Coverage for commands that simply delegate to the underlying Click CLI."""

    @pytest.mark.parametrize(
        "command,expected_args",
        [
            ("/onboard", ["onboard"]),
            ("/deploy ec2", ["deploy", "ec2"]),
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
