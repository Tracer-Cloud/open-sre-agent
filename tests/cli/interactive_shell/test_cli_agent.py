"""Tests for the LangGraph-free interactive-shell assistant.

Covers:

- terminology: the LLM is instructed to call this surface the "interactive
  shell" and is forbidden from using "REPL" in user-facing answers (#604);
- formatting: assistant Markdown output is rendered through Rich's Markdown
  renderer so tables / **bold** / `code` display correctly in the terminal
  instead of leaking raw Markdown syntax (#604).
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console

from app.cli.interactive_shell import cli_agent
from app.cli.interactive_shell.cli_agent import (
    _ACTION_RULE,
    _MARKDOWN_RULE,
    _TERMINOLOGY_RULE,
    _build_system_prompt,
    _parse_action_plan,
    answer_cli_agent,
)
from app.cli.interactive_shell.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    # ``force_terminal=True`` so Rich emits its real renderer output (the
    # same path the user sees) rather than collapsing markdown into raw
    # text on a non-tty stream.
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLMClient:
    def __init__(self, content: str) -> None:
        self._content = content
        self.last_prompt: str | None = None

    def invoke(self, prompt: str) -> _FakeLLMResponse:
        self.last_prompt = prompt
        return _FakeLLMResponse(self._content)


def _patch_llm(monkeypatch: Any, content: str) -> _FakeLLMClient:
    client = _FakeLLMClient(content)
    # ``answer_cli_agent`` imports ``get_llm_for_reasoning`` lazily from
    # ``app.services.llm_client``, so we patch the symbol on that module.
    import app.services.llm_client as llm_module

    monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: client)
    return client


class TestSystemPromptTerminology:
    """The LLM grounding must steer answers away from the word 'REPL'."""

    def test_conversational_prompt_uses_interactive_shell_not_repl(self) -> None:
        prompt = _build_system_prompt("conversational", reference="(ref)", history="(hist)")
        assert "interactive shell" in prompt
        # The prompt must explicitly forbid the "REPL" jargon so the model
        # does not echo it back in answers (#604).
        assert _TERMINOLOGY_RULE in prompt
        assert "Never use the word 'REPL'" in prompt

    def test_reference_only_prompt_uses_interactive_shell_not_repl(self) -> None:
        prompt = _build_system_prompt("reference_only", reference="(ref)", history="(hist)")
        assert "interactive shell" in prompt
        assert _TERMINOLOGY_RULE in prompt
        assert "Never use the word 'REPL'" in prompt

    def test_both_prompts_request_markdown_formatting(self) -> None:
        for mode in ("conversational", "reference_only"):
            prompt = _build_system_prompt(mode, reference="(ref)", history="(hist)")  # type: ignore[arg-type]
            assert _MARKDOWN_RULE in prompt
            assert "Markdown" in prompt

    def test_conversational_prompt_exposes_action_contract(self) -> None:
        prompt = _build_system_prompt("conversational", reference="(ref)", history="(hist)")

        assert _ACTION_RULE in prompt
        assert "switch_llm_provider" in prompt
        assert '"action":"switch_llm_provider"' in prompt


class TestActionPlanParsing:
    def test_parses_prose_wrapped_json(self) -> None:
        actions = _parse_action_plan(
            """
            Here is the JSON response:

            {
              "actions": [
                {"action": "switch_llm_provider", "provider": "anthropic", "model": ""}
              ]
            }
            """
        )

        assert actions == [{"action": "switch_llm_provider", "provider": "anthropic", "model": ""}]

    def test_infers_provider_switch_action_when_action_field_is_missing(self) -> None:
        actions = _parse_action_plan(
            """
            To switch to Anthropic:
            {
              "actions": [
                {"provider": "anthropic", "model": ""}
              ]
            }
            """
        )

        assert actions == [{"action": "switch_llm_provider", "provider": "anthropic", "model": ""}]

    def test_parses_single_action_object(self) -> None:
        actions = _parse_action_plan(
            """
            Here is the JSON response for the requested action:

            {"action":"switch_llm_provider","provider":"anthropic","model":""}
            """
        )

        assert actions == [{"action": "switch_llm_provider", "provider": "anthropic", "model": ""}]


class TestAssistantOutputRendering:
    """The assistant reply must be rendered, not printed as raw Markdown."""

    def test_bold_markdown_is_rendered(self, monkeypatch: Any) -> None:
        # Use force_terminal=True console so Rich renders ANSI for bold.
        # We strip ANSI escapes from the output to assert the visible chars.
        _patch_llm(monkeypatch, "Hello **world**")
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        # Markdown rendering removes the literal ** delimiters around "world".
        assert "**world**" not in output
        assert "world" in output
        assert "Hello" in output

    def test_table_markdown_is_rendered_as_table(self, monkeypatch: Any) -> None:
        markdown = (
            "| Command | What it does |\n|---|---|\n| `agent` | Launch the interactive shell |\n"
        )
        _patch_llm(monkeypatch, markdown)
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("show commands", session, console)
        output = _strip_ansi(buf.getvalue())
        # Raw markdown table separator must not leak through.
        assert "|---|---|" not in output
        # Header / cell text must be preserved (Rich uses box-drawing chars,
        # so the text is what matters here, not the exact column dividers).
        assert "Command" in output
        assert "What it does" in output
        assert "agent" in output

    def test_response_is_recorded_in_session_history(self, monkeypatch: Any) -> None:
        _patch_llm(monkeypatch, "Sure thing.")
        session = ReplSession()
        console, _ = _capture()
        answer_cli_agent("hello", session, console)
        assert session.cli_agent_messages[-2:] == [
            ("user", "hello"),
            ("assistant", "Sure thing."),
        ]

    def test_llm_failure_prints_red_error_and_does_not_record(self, monkeypatch: Any) -> None:
        class _Boom:
            def invoke(self, prompt: str) -> Any:  # noqa: ARG002
                raise RuntimeError("upstream 503")

        import app.services.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Boom())
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "assistant failed" in output
        assert "upstream 503" in output
        # On failure the turn must NOT be appended to the cli-agent history,
        # otherwise the next turn's prompt would carry a phantom assistant
        # message.
        assert session.cli_agent_messages == []

    def test_reasoned_provider_switch_action_is_executed(
        self,
        monkeypatch: Any,
        tmp_path: Any,
    ) -> None:
        _patch_llm(
            monkeypatch,
            '{"actions":[{"action":"switch_llm_provider","provider":"anthropic"}]}',
        )

        import app.cli.interactive_shell.commands as command_module
        import app.cli.wizard.env_sync as env_sync

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-sonnet-4-6"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(command_module, "_load_llm_settings", lambda: _Fake())

        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("switch back to anthropic", session, console)

        output = _strip_ansi(buf.getvalue())
        assert "Requested actions" in output
        assert "$ /model set anthropic" in output
        assert "switched LLM provider" in output
        assert "LLM_PROVIDER=anthropic" in (tmp_path / ".env").read_text(encoding="utf-8")
        assert session.history[-1] == {"type": "slash", "text": "/model set anthropic", "ok": True}

    def test_prose_wrapped_provider_only_action_is_executed(
        self,
        monkeypatch: Any,
        tmp_path: Any,
    ) -> None:
        _patch_llm(
            monkeypatch,
            """
            Here is the JSON response for the requested action:

            {
              "actions": [
                {
                  "provider": "anthropic",
                  "model": ""
                }
              ]
            }
            """,
        )

        import app.cli.interactive_shell.commands as command_module
        import app.cli.wizard.env_sync as env_sync

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-sonnet-4-6"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(command_module, "_load_llm_settings", lambda: _Fake())

        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("switch to the anthropic model", session, console)

        output = _strip_ansi(buf.getvalue())
        assert "Here is the JSON response" not in output
        assert "$ /model set anthropic" in output
        assert "switched LLM provider" in output


class TestLoaderWiring:
    """The orange spinner must surround every LLM call from the assistant."""

    def test_llm_invocation_is_wrapped_in_orange_loader(self, monkeypatch: Any) -> None:
        from contextlib import contextmanager

        events: list[str] = []

        @contextmanager
        def _spy_loader(_console: Console, label: str = "thinking") -> Any:
            events.append(f"enter:{label}")
            try:
                yield
            finally:
                events.append("exit")

        monkeypatch.setattr(cli_agent, "llm_loader", _spy_loader)

        class _Recording:
            def invoke(self, prompt: str) -> _FakeLLMResponse:  # noqa: ARG002
                events.append("invoke")
                return _FakeLLMResponse("ok")

        import app.services.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Recording())

        console, _ = _capture()
        answer_cli_agent("hi", ReplSession(), console)

        # The LLM call MUST happen inside the loader's context; the order
        # is ``enter -> invoke -> exit``.
        assert events == ["enter:thinking", "invoke", "exit"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so assertions test the visible output."""
    import re

    # Standard CSI-sequence regex; covers Rich's bold / color escapes.
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def test_module_exports_answer_cli_agent() -> None:
    assert "answer_cli_agent" in cli_agent.__all__
