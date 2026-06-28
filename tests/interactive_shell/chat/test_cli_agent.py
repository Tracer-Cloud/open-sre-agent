"""Tests for the interactive-shell CLI assistant.

Covers:

- terminology: the LLM is instructed to call this surface the "interactive
  shell" and is forbidden from using "REPL" in user-facing answers (#604);
- formatting: assistant Markdown output is rendered through Rich's Markdown
  renderer so tables / **bold** / `code` display correctly in the terminal
  instead of leaking raw Markdown syntax (#604).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rich.console import Console

from core.agent.action_plan import ActionPlanAction
from core.agent.action_plan import parse_action_plan as _parse_action_plan
from core.agent.prompts.assistant_agent_prompt import (
    _ACTION_RULE,
    _MARKDOWN_RULE,
    _TERMINOLOGY_RULE,
    _build_observation_block,
    _build_system_prompt,
    build_environment_block,
)
from interactive_shell.agent_shell import adapters as shell_adapters
from interactive_shell.agent_shell import agent as cli_agent
from interactive_shell.agent_shell.agent import answer_cli_agent
from interactive_shell.session import ReplSession


def _build_environment_block(session: ReplSession) -> str:
    """Adapter for the relocated, signature-changed environment-block builder."""
    return build_environment_block(
        integrations=tuple(session.configured_integrations),
        known=session.configured_integrations_known,
    )


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    # ``force_terminal=True`` so Rich emits its real renderer output (the
    # same path the user sees) rather than collapsing markdown into raw
    # text on a non-tty stream.
    return (
        Console(file=buf, force_terminal=True, color_system=None, width=80, highlight=False),
        buf,
    )


class _FakeLLMClient:
    """Streaming-aware fake.

    ``invoke_stream`` yields the canned content as a single chunk. ``content``
    accepts either a plain string or an Anthropic-style list of content blocks
    (objects with ``.text`` or dicts with a ``"text"`` key); blocks are flattened
    to the same text the real SDK's ``text_stream`` would surface.
    """

    def __init__(self, content: Any) -> None:
        self._content = content
        self.last_prompt: str | None = None

    def invoke_stream(self, prompt: str) -> Iterator[str]:
        self.last_prompt = prompt
        if isinstance(self._content, list):
            parts: list[str] = []
            for block in self._content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    parts.append(block.text)
            yield "\n".join(parts)
            return
        yield str(self._content)


def _patch_llm(monkeypatch: Any, content: Any) -> _FakeLLMClient:
    client = _FakeLLMClient(content)
    # ``answer_cli_agent`` imports ``get_llm_for_reasoning`` lazily from
    # ``core.llm.llm_client``, so we patch the symbol on that module.
    import core.llm.llm_client as llm_module

    monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: client)
    return client


def _patch_grounding(
    monkeypatch: Any,
    *,
    cli_reference: str = "(ref)",
    agents_md: str = "",
    investigation_flow: str = "",
) -> None:
    """Pin the shell grounding caches the prompt provider reads from.

    The conversational assistant now sources grounding text through
    ``ShellPromptContextProvider`` (over ``session.grounding`` + the
    investigation-flow reference), so tests patch the provider methods rather
    than module-level builders.
    """
    provider = shell_adapters.ShellPromptContextProvider
    monkeypatch.setattr(provider, "cli_reference", lambda _self: cli_reference)
    monkeypatch.setattr(provider, "agents_md", lambda _self: agents_md)
    monkeypatch.setattr(provider, "investigation_flow", lambda _self: investigation_flow)


class TestSystemPromptTerminology:
    """The LLM grounding must steer answers away from the word 'REPL'."""

    def test_conversational_prompt_uses_interactive_shell_not_repl(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "interactive shell" in prompt
        assert "argv" in prompt
        assert "!" in prompt
        # The prompt must explicitly forbid the "REPL" jargon so the model
        # does not echo it back in answers (#604).
        assert _TERMINOLOGY_RULE in prompt
        assert "Never use the word 'REPL'" in prompt

    def test_prompt_requests_markdown_formatting(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert _MARKDOWN_RULE in prompt
        assert "Markdown" in prompt

    def test_conversational_prompt_exposes_action_contract(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")

        assert _ACTION_RULE in prompt
        assert "switch_llm_provider" in prompt
        assert '"action":"switch_llm_provider"' in prompt
        assert "claude-code" in prompt
        assert "gemini-cli" in prompt
        assert "antigravity-cli" in prompt

    def test_prompt_gives_generic_integration_setup_guidance(self) -> None:
        """Any "configure/connect X" request should LAUNCH setup via a run_interactive
        action rather than just printing a command — and generically, not per vendor
        (see the "can you configure sentry?" deflection)."""
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "/integrations setup <service>" in prompt
        assert "run_interactive" in prompt
        # Launch it for them, do not merely instruct.
        assert "do NOT just" in prompt
        # Catch-all phrasing, not vendor-specific hardcoding.
        assert "any integration" in prompt
        assert "never hardcode advice to one vendor" in prompt


class TestSystemPromptAgentsMdGrounding:
    """The conversational shell wires AGENTS.md repo-map content (#1442)."""

    def test_section_present_in_conversational_prompt_when_agents_md_provided(self) -> None:
        prompt = _build_system_prompt(
            reference="(ref)",
            history="(hist)",
            agents_md="repo map content",
        )
        assert "--- Repo map (AGENTS.md) ---" in prompt
        assert "repo map content" in prompt

    def test_section_omitted_when_agents_md_empty(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)", agents_md="")
        assert "--- Repo map (AGENTS.md) ---" not in prompt

    def test_section_omitted_by_default_for_callers_that_dont_pass_it(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)")
        assert "--- Repo map (AGENTS.md) ---" not in prompt


class TestSystemPromptInvestigationFlowGrounding:
    """The conversational shell includes the investigation-flow reference block."""

    def test_investigation_flow_section_present_when_reference_provided(self) -> None:
        prompt = _build_system_prompt(
            reference="(ref)",
            history="(hist)",
            investigation_flow="resolve → extract → investigate → deliver",
        )

        assert "--- Investigation flow reference ---" in prompt
        assert "resolve → extract → investigate → deliver" in prompt
        assert "do not claim the pipeline definition is unavailable" in prompt

    def test_investigation_flow_section_omitted_when_reference_empty(self) -> None:
        prompt = _build_system_prompt(reference="(ref)", history="(hist)", investigation_flow="")

        assert "--- Investigation flow reference ---" not in prompt

    def test_answer_cli_agent_injects_investigation_flow_reference(self, monkeypatch: Any) -> None:
        client = _patch_llm(monkeypatch, "Yes, I can describe the pipeline.")
        _patch_grounding(
            monkeypatch,
            investigation_flow="resolve → extract → investigate → deliver",
        )

        console, _ = _capture()
        answer_cli_agent("Can you see how investigations are structured?", ReplSession(), console)

        assert client.last_prompt is not None
        assert "--- Investigation flow reference ---" in client.last_prompt
        assert "resolve → extract → investigate → deliver" in client.last_prompt


class TestEnvironmentIntegrationGrounding:
    """The assistant must be told which integrations are configured (#sentry-context)."""

    def test_block_lists_configured_services_when_known(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ("gitlab", "datadog")
        block = _build_environment_block(session)
        assert "--- Environment (configured integrations) ---" in block
        assert "gitlab" in block
        assert "datadog" in block
        assert "not in that list is NOT configured" in block

    def test_block_states_none_when_known_and_empty(self) -> None:
        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ()
        block = _build_environment_block(session)
        assert "No integrations are configured" in block

    def test_block_omitted_when_unknown(self) -> None:
        session = ReplSession()
        assert session.configured_integrations_known is False
        assert _build_environment_block(session) == ""

    def test_answer_cli_agent_injects_configured_integrations(self, monkeypatch: Any) -> None:
        client = _patch_llm(monkeypatch, "No, Sentry is not configured.")
        _patch_grounding(monkeypatch)

        session = ReplSession()
        session.configured_integrations_known = True
        session.configured_integrations = ("gitlab",)
        console, _ = _capture()
        answer_cli_agent("is sentry installed?", session, console)

        assert client.last_prompt is not None
        assert "--- Environment (configured integrations) ---" in client.last_prompt
        assert "gitlab" in client.last_prompt


class TestObservationSummaryBlock:
    """The observe→answer loop feeds discovery output back for summarization."""

    def test_block_empty_without_observation(self) -> None:
        assert _build_observation_block(None) == ""
        assert _build_observation_block("   ") == ""

    def test_block_wraps_command_output_with_summarize_instruction(self) -> None:
        block = _build_observation_block("- sentry: missing (Not configured.)")
        assert "tool_results" in block
        assert "- sentry: missing (Not configured.)" in block
        assert "summarize" in block.lower()
        # The summary turn must not kick off more actions.
        assert "not request, plan, or emit any further actions" in block.lower()

    def test_answer_cli_agent_injects_observation(self, monkeypatch: Any) -> None:
        client = _patch_llm(monkeypatch, "No — Sentry is not configured.")
        _patch_grounding(monkeypatch)

        session = ReplSession()
        console, _ = _capture()
        observation = (
            "Integration status from `/integrations`:\n- sentry: missing (Not configured.)"
        )
        answer_cli_agent("is sentry installed?", session, console, tool_observation=observation)

        assert client.last_prompt is not None
        assert "tool_results" in client.last_prompt
        assert "sentry: missing" in client.last_prompt


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

        assert actions == (ActionPlanAction(kind="switch_llm_provider", provider="anthropic"),)

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

        assert actions == (ActionPlanAction(kind="switch_llm_provider", provider="anthropic"),)

    def test_parses_single_action_object(self) -> None:
        actions = _parse_action_plan(
            """
            Here is the JSON response for the requested action:

            {"action":"switch_llm_provider","provider":"anthropic","model":""}
            """
        )

        assert actions == (ActionPlanAction(kind="switch_llm_provider", provider="anthropic"),)


class TestAssistantOutputRendering:
    """The assistant reply must be rendered, not printed as raw Markdown."""

    def test_bold_markdown_is_rendered(self, monkeypatch: Any) -> None:
        # End-of-stream force-flush renders the buffered text as
        # Markdown — ``**`` delimiters are stripped.
        _patch_llm(monkeypatch, "Hello **world**")
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "**world**" not in output
        assert "world" in output
        assert "Hello" in output
        assert session.token_usage.get("output", 0) > 0

    def test_table_markdown_is_rendered_as_table(self, monkeypatch: Any) -> None:
        markdown = (
            "| Command | What it does |\n|---|---|\n"
            "| `opensre` | Start the interactive shell (TTY) |\n"
        )
        _patch_llm(monkeypatch, markdown)
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("show commands", session, console)
        output = _strip_ansi(buf.getvalue())
        # Rich's Markdown table renderer replaces the ``|---|---|``
        # separator with box-drawing chars — the literal must not leak.
        assert "|---|---|" not in output
        assert "Command" in output
        assert "What it does" in output
        assert "opensre" in output

    def test_response_is_recorded_in_session_history(self, monkeypatch: Any) -> None:
        _patch_llm(monkeypatch, "Sure thing.")
        session = ReplSession()
        console, _ = _capture()
        answer_cli_agent("hello", session, console)
        assert session.cli_agent_messages[-2:] == [
            ("user", "hello"),
            ("assistant", "Sure thing."),
        ]

    def test_command_selection_prompt_uses_llm_response(self, monkeypatch: Any) -> None:
        _patch_llm(monkeypatch, "Use `opensre investigate` for incidents.")
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("what command do I use?", session, console)
        output = _strip_ansi(buf.getvalue()).casefold()
        assert "opensre investigate" in output
        assert session.cli_agent_messages[-2:] == [
            ("user", "what command do I use?"),
            ("assistant", "Use `opensre investigate` for incidents."),
        ]
        assert session.llm_call_count == 1

    def test_structured_content_blocks_are_rendered(self, monkeypatch: Any) -> None:
        class _Block:
            def __init__(self, text: str) -> None:
                self.text = text

        _patch_llm(monkeypatch, [_Block("First line"), {"text": "Second line"}])
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("hello", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "First line" in output
        assert "Second line" in output
        assert session.cli_agent_messages[-1] == ("assistant", "First line\nSecond line")

    def test_llm_failure_prints_red_error_and_does_not_record(self, monkeypatch: Any) -> None:
        captured_errors: list[BaseException] = []

        class _Boom:
            def invoke_stream(self, _prompt: str) -> Iterator[str]:
                raise RuntimeError("upstream 503")
                yield  # pragma: no cover  -- generator marker

        import core.llm.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Boom())
        monkeypatch.setattr(
            "interactive_shell.utils.error_handling.exception_reporting.capture_exception",
            lambda exc, **_kwargs: captured_errors.append(exc),
        )
        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("hi", session, console)
        output = _strip_ansi(buf.getvalue())
        assert "assistant failed" in output
        assert "upstream 503" in output
        assert len(captured_errors) == 1
        assert isinstance(captured_errors[0], RuntimeError)
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

        import cli.wizard.env_sync as env_sync
        from interactive_shell.command_registry import repl_data as repl_data_module

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-sonnet-4-6"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())
        # /model set now requires the target provider's credential to exist;
        # provide one so the cli-agent's planned switch actually runs.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("switch back to anthropic", session, console)

        output = _strip_ansi(buf.getvalue())
        assert "Requested actions" in output
        assert "$ /model set anthropic" in output
        assert "switched LLM provider" in output
        assert "LLM_PROVIDER=anthropic" in (tmp_path / ".env").read_text(encoding="utf-8")
        assert session.history[-1] == {"type": "slash", "text": "/model set anthropic", "ok": True}

    def test_provider_switch_blocked_when_llm_provider_capability_disabled(
        self,
        monkeypatch: Any,
        tmp_path: Any,
    ) -> None:
        """A session that explicitly disables the ``llm_provider`` surface must
        not actuate a switch_llm_provider action from the chat answer path.

        This is the deterministic guard behind scenario 700: pasted meta-text
        that quotes an example command (``switch my model to gpt-5.5``) can bait
        the assistant into emitting a provider-switch action. When the surface is
        pinned off, the action is dropped before execution -- no ``/model set``
        runs, no ``.env`` write, and no ``slash`` history entry is recorded."""
        _patch_llm(
            monkeypatch,
            '{"actions":[{"action":"switch_llm_provider","provider":"openai","model":"gpt-5.5"}]}',
        )

        import cli.wizard.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)

        session = ReplSession(available_capabilities={"llm_provider": ()})
        console, buf = _capture()
        answer_cli_agent("switch my model to gpt-5.5", session, console)

        output = _strip_ansi(buf.getvalue())
        assert "$ /model set" not in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()
        assert all(entry.get("type") != "slash" for entry in session.history)

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

        import cli.wizard.env_sync as env_sync
        from interactive_shell.command_registry import repl_data as repl_data_module

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-sonnet-4-6"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("switch to the anthropic model", session, console)

        output = _strip_ansi(buf.getvalue())
        # With streaming, prose-prefixed responses are rendered live before the
        # action card — suppression only triggers when the response *starts*
        # with ``{``. Surfacing the model's reasoning is preferred over the
        # blocking-era behavior of hiding it (#1263).
        assert "Here is the JSON response" in output
        assert "$ /model set anthropic" in output
        assert "switched LLM provider" in output


class TestStreamingMigration:
    """cli_agent must consume invoke_stream and send output through the shared streaming renderer."""

    def test_response_uses_invoke_stream_not_invoke(self, monkeypatch: Any) -> None:
        calls: list[str] = []

        class _Recording:
            def invoke(self, _prompt: str) -> Any:
                calls.append("invoke")
                raise AssertionError("cli_agent must not call invoke after streaming migration")

            def invoke_stream(self, _prompt: str) -> Iterator[str]:
                calls.append("invoke_stream")
                yield "ok"

        import core.llm.llm_client as llm_module

        monkeypatch.setattr(llm_module, "get_llm_for_reasoning", lambda: _Recording())

        console, _ = _capture()
        answer_cli_agent("hi", ReplSession(), console)

        assert calls == ["invoke_stream"]

    def test_json_action_response_does_not_leak_to_live_region(
        self,
        monkeypatch: Any,
        tmp_path: Any,
    ) -> None:
        """A JSON action plan must not surface as raw braces in the live render.

        Suppression peeks the first non-whitespace char; if it is ``{``, the
        helper drains silently and the action card prints in its place.
        """
        _patch_llm(
            monkeypatch,
            '{"actions":[{"action":"switch_llm_provider","provider":"anthropic"}]}',
        )

        import cli.wizard.env_sync as env_sync
        from interactive_shell.command_registry import repl_data as repl_data_module

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-sonnet-4-6"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        session = ReplSession()
        console, buf = _capture()
        answer_cli_agent("switch to anthropic", session, console)

        output = _strip_ansi(buf.getvalue())
        # Suppression: the raw JSON payload must not appear in the rendered
        # output; only the action card is visible.
        assert '{"actions"' not in output
        assert '"switch_llm_provider"' not in output
        # The action card is unchanged from pre-streaming behavior.
        assert "Requested actions" in output
        assert "$ /model set anthropic" in output


def test_answer_cli_agent_injects_synthetic_observation_on_why_failed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    obs = tmp_path / "latest.json"
    obs.write_text(
        '{"scenario_id": "008-storage-full-missing-metric", "score": {"passed": false}}',
        encoding="utf-8",
    )
    session = ReplSession()
    session.last_synthetic_observation_path = str(obs.resolve())
    console, _buf = _capture()
    client = _patch_llm(monkeypatch, "The synthetic run failed the scoring gate.")
    answer_cli_agent("why did it fail?", session, console)
    assert client.last_prompt is not None
    assert "observation_json" in client.last_prompt
    assert "008-storage-full-missing-metric" in client.last_prompt


def test_answer_cli_agent_skips_observation_without_failure_question(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    obs = tmp_path / "latest.json"
    obs.write_text("{}", encoding="utf-8")
    session = ReplSession()
    session.last_synthetic_observation_path = str(obs.resolve())
    console, _buf = _capture()
    client = _patch_llm(monkeypatch, "hi")
    answer_cli_agent("hello", session, console)
    assert client.last_prompt is not None
    assert "observation_json" not in client.last_prompt


def test_run_interactive_action_queues_setup_command(monkeypatch: Any) -> None:
    """'can you configure sentry?' should LAUNCH setup (queue it for auto-submit),
    not just print a command for the user to type."""
    _patch_llm(
        monkeypatch,
        '{"actions":[{"action":"run_interactive","command":"/integrations setup sentry"}]}',
    )
    monkeypatch.setattr(
        "interactive_shell.ui.components.choice_menu.repl_tty_interactive", lambda: True
    )
    session = ReplSession()
    console, buf = _capture()
    answer_cli_agent("can you configure sentry?", session, console)
    assert session.pending_prompt_default == "/integrations setup sentry"
    assert session.pending_prompt_autosubmit is True
    assert "Launching" in _strip_ansi(buf.getvalue())


def test_run_interactive_action_falls_back_to_guidance_without_tty(monkeypatch: Any) -> None:
    """Without an interactive prompt to submit into (scripted/non-TTY), the action
    degrades to telling the user the command rather than silently no-op'ing."""
    _patch_llm(
        monkeypatch,
        '{"actions":[{"action":"run_interactive","command":"/integrations setup sentry"}]}',
    )
    monkeypatch.setattr(
        "interactive_shell.ui.components.choice_menu.repl_tty_interactive", lambda: False
    )
    session = ReplSession()
    console, buf = _capture()
    answer_cli_agent("can you configure sentry?", session, console)
    assert session.pending_prompt_default is None
    assert session.pending_prompt_autosubmit is False
    assert "/integrations setup sentry" in _strip_ansi(buf.getvalue())


def test_run_interactive_action_queues_any_registered_opensre_command(monkeypatch: Any) -> None:
    _patch_llm(
        monkeypatch,
        '{"actions":[{"action":"run_interactive","command":"/integrations remove github"}]}',
    )
    monkeypatch.setattr(
        "interactive_shell.ui.components.choice_menu.repl_tty_interactive", lambda: True
    )
    session = ReplSession()
    console, buf = _capture()
    answer_cli_agent("remove github connection", session, console)
    assert session.pending_prompt_default == "/integrations remove github"
    assert session.pending_prompt_autosubmit is True
    assert "Launching" in _strip_ansi(buf.getvalue())


def test_run_interactive_action_rejects_unknown_slash_command(monkeypatch: Any) -> None:
    _patch_llm(
        monkeypatch,
        '{"actions":[{"action":"run_interactive","command":"/not-an-opensre-command now"}]}',
    )
    monkeypatch.setattr(
        "interactive_shell.ui.components.choice_menu.repl_tty_interactive", lambda: True
    )
    session = ReplSession()
    console, buf = _capture()
    answer_cli_agent("do a fake thing", session, console)
    assert session.pending_prompt_default is None
    assert "unsupported interactive command" in _strip_ansi(buf.getvalue())


def test_prompt_advertises_run_interactive_for_configure_requests() -> None:
    """The system prompt must tell the model to LAUNCH setup via run_interactive,
    catch-all for any integration (no hardcoded vendor advice)."""
    prompt = _build_system_prompt(reference="(ref)", history="(hist)")
    assert "run_interactive" in prompt
    assert "/integrations setup <service>" in prompt
    assert "any integration" in prompt
    assert "never hardcode advice to one vendor" in prompt


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
