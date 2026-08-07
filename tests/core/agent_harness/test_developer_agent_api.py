"""Developer journey: create and drive your own agent on the Python API.

Pins the documented ladder in ``docs/python-api.mdx`` — two-line start, custom
sink, custom grounding, multi-turn reuse — without a live LLM provider.

The action planner still talks to the real provider when tools are empty, so
these scenarios stub ``ActionTurnRunner.run`` and inject a streaming Echo
client on the reasoning port. That isolates the embedder-facing seams.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.turns.default_headless_agent import build_default_headless_agent
from core.agent_harness.turns.gather_ports import GATHER_DISABLED
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    NullToolProvider,
    StaticReasoningClientProvider,
)
from core.agent_harness.turns.headless_dispatch import HeadlessAgent
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


class _EchoClient:
    """Streaming client that mirrors persona markers — no network."""

    def invoke_stream(self, prompt: Any) -> Any:
        from integrations.llm_cli.text import flatten_messages_to_prompt

        # Answer path passes ``AssistantTurnPrompt.messages()`` (system+user),
        # not a single joined string — scan content, not list membership.
        text = flatten_messages_to_prompt(prompt)
        marker = "CUSTOM PERSONA" if "CUSTOM PERSONA" in text else "ok"
        yield f"echo:{marker}"


class _RecordingPrompts(EmptyPromptContextProvider):
    """Caller's grounding: records which corpora the answer path asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def agents_md(self) -> str:
        self.calls.append("agents_md")
        return "CUSTOM PERSONA"

    def cli_reference(self) -> str:
        self.calls.append("cli_reference")
        return ""

    def docs(self, query: str) -> str:
        self.calls.append(f"docs:{query}")
        return ""

    def investigation_flow(self) -> str:
        self.calls.append("investigation_flow")
        return ""

    def runtime_facts(self) -> Mapping[str, Any]:
        self.calls.append("runtime_facts")
        return {}

    def environment_block(self, runtime: Mapping[str, Any] | None = None) -> str:
        _ = runtime
        self.calls.append("environment_block")
        return ""


def _headless_config(**overrides: Any) -> SessionConfig:
    """No env / storage / integrations — embedder unit-test shape."""
    return SessionConfig(
        load_env=False,
        hydrate_integrations=False,
        persistent_tasks=False,
        open_storage=False,
        **overrides,
    )


def _unhandled_actions(*_args: Any, **_kwargs: Any) -> ToolCallingTurnResult:
    """Leave the turn for the conversational answer path."""
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
    )


@pytest.fixture
def stub_action_planner(monkeypatch: Any) -> None:
    """Keep the action planner off the network so Echo can answer."""
    monkeypatch.setattr(
        "core.agent_harness.turns.headless_dispatch.ActionTurnRunner.run",
        lambda _self, *_args, **_kwargs: _unhandled_actions(),
    )


def _controlled_agent(
    *,
    output: Any | None = None,
    prompts: Any | None = None,
) -> tuple[HeadlessAgent, Any]:
    sink = output if output is not None else BufferOutputSink()
    agent = HeadlessAgent(
        tools=NullToolProvider(),
        output=sink,
        prompts=prompts if prompts is not None else EmptyPromptContextProvider(),
        reasoning=StaticReasoningClientProvider(client=_EchoClient()),
        gather=GATHER_DISABLED,
    )
    return agent, sink


def test_two_line_api_dispatches_an_answer(stub_action_planner: None) -> None:
    """``AgentSession`` + ``chat`` is the documented happy path."""
    # Arrange
    harness = AgentSession(_headless_config())
    agent, _sink = _controlled_agent()
    harness.attach_agent(agent)

    # Act
    result = harness.chat("why is checkout-api slow?")

    # Assert
    assert isinstance(result, TurnResult)
    assert result.answered
    assert "echo:" in (result.primary_response_text or "")


def test_follow_up_reuses_the_same_attached_agent(stub_action_planner: None) -> None:
    """Each ``chat`` is one turn on the same agent/session."""
    # Arrange
    harness = AgentSession(_headless_config())
    agent, _sink = _controlled_agent()
    harness.attach_agent(agent)

    # Act
    first = harness.chat("list open incidents")
    second = harness.chat("which affect checkout?")

    # Assert
    assert harness.agent is agent
    assert first.answered and second.answered
    assert first.primary_response_text != ""
    assert second.primary_response_text != ""


def test_documented_custom_sink_path_captures_streamed_answer(
    stub_action_planner: None,
) -> None:
    """``startup`` → ``build_default_headless_agent(output=…)`` → ``attach_agent``."""
    # Arrange
    harness = AgentSession(_headless_config())
    startup = harness.startup()
    sink = BufferOutputSink()
    agent = build_default_headless_agent(session=startup.session, output=sink)
    agent._tools = NullToolProvider()  # noqa: SLF001
    agent._reasoning = StaticReasoningClientProvider(client=_EchoClient())  # noqa: SLF001
    agent._gather_ports = GATHER_DISABLED  # noqa: SLF001
    harness.attach_agent(agent)

    # Act
    result = harness.chat("summarize open incidents")

    # Assert
    assert result.answered
    assert sink.streamed, "BufferOutputSink.streamed must collect answer chunks"
    assert any("echo:" in chunk for chunk in sink.streamed)


def test_start_honours_caller_grounding_provider(stub_action_planner: None) -> None:
    """``SessionConfig.prompts`` must be the provider the answer path uses."""
    # Arrange
    prompts = _RecordingPrompts()
    harness = AgentSession.start(_headless_config(prompts=prompts))
    assert harness.agent is not None
    harness.agent._tools = NullToolProvider()  # noqa: SLF001
    harness.agent._reasoning = StaticReasoningClientProvider(client=_EchoClient())  # noqa: SLF001
    harness.agent._gather_ports = GATHER_DISABLED  # noqa: SLF001

    # Act
    result = harness.chat("what is our on-call policy?")

    # Assert
    assert harness.agent._prompts is prompts  # noqa: SLF001
    assert "agents_md" in prompts.calls
    assert result.answered
    assert "CUSTOM PERSONA" in (result.primary_response_text or "")


def test_builder_accepts_caller_grounding_on_the_second_path() -> None:
    """The explicit ``build_default_headless_agent`` path must take ``prompts=``."""
    # Arrange
    harness = AgentSession(_headless_config())
    startup = harness.startup()
    prompts = _RecordingPrompts()
    sink = BufferOutputSink()

    # Act
    agent = build_default_headless_agent(
        session=startup.session,
        output=sink,
        prompts=prompts,
    )

    # Assert — wired before any dispatch
    assert agent._prompts is prompts  # noqa: SLF001


def test_custom_output_sink_protocol_is_enough(stub_action_planner: None) -> None:
    """Any ``OutputSink`` works — not only ``BufferOutputSink``."""

    class _ListSink:
        def __init__(self) -> None:
            self.lines: list[str] = []
            self.streamed: list[str] = []

        def print(self, message: str = "") -> None:
            self.lines.append(message)

        def render_response_header(self, label: str) -> None:
            self.lines.append(f"[{label}]")

        def render_error(self, message: str) -> None:
            self.lines.append(f"ERROR: {message}")

        def stream(
            self,
            *,
            label: str,
            chunks: Any,
            suppress_if_starts_with: str | None = None,
            defer_want_me_to_closer: bool = False,
        ) -> str:
            _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
            text = "".join(str(c) for c in chunks)
            self.streamed.append(text)
            return text

    # Arrange
    sink = _ListSink()
    harness = AgentSession(_headless_config())
    agent, _ = _controlled_agent(output=sink)
    harness.attach_agent(agent)

    # Act
    result = harness.chat("ping")

    # Assert
    assert result.answered
    assert sink.streamed


def test_start_without_prompts_keeps_default_grounding() -> None:
    """Omitting ``prompts`` must not leave the agent ungrounded."""
    # Arrange / Act
    harness = AgentSession.start(_headless_config())

    # Assert
    assert harness.agent is not None
    assert type(harness.agent._prompts).__name__ == "DefaultPromptContextProvider"  # noqa: SLF001


def test_resume_config_reaches_session_manager() -> None:
    """``SessionConfig.session_id`` is how a developer resumes a conversation."""
    # Arrange
    from surfaces.interactive_shell.session import Session

    class _FakeSessionManager:
        def __init__(self) -> None:
            self.session = Session()
            self.resolve_calls: list[dict[str, Any]] = []

        def create(self, **_kwargs: Any) -> Session:
            raise AssertionError("resume must call resolve, not create")

        def resolve(self, session_id: str, **kwargs: Any) -> Session:
            self.resolve_calls.append({"session_id": session_id, **kwargs})
            return self.session

    manager = _FakeSessionManager()
    harness = AgentSession(
        SessionConfig(
            session_id="abc123",
            load_env=False,
            hydrate_integrations=False,
            persistent_tasks=False,
            open_storage=False,
            session_manager=manager,  # type: ignore[arg-type]
        )
    )

    # Act
    startup = harness.startup()

    # Assert
    assert startup.session is manager.session
    assert manager.resolve_calls[0]["session_id"] == "abc123"


def test_every_exported_name_is_lazily_resolvable_and_type_checked() -> None:
    """The three export lists in ``core.agent_harness`` must not drift apart.

    A public name lives in three places: ``_LAZY_EXPORTS`` (what ``__getattr__``
    can resolve), ``__all__`` (what the package advertises), and the
    ``TYPE_CHECKING`` block (what type checkers and static analysis can see).
    ``DefaultPromptContextProvider`` was added to the first two but not the
    third, so it resolved at runtime while reading as undefined to every static
    tool looking at the package.
    """
    # Arrange
    import ast
    from pathlib import Path

    import core.agent_harness as harness

    source = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    type_checked: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", "") == "TYPE_CHECKING":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.ImportFrom):
                    type_checked.update(a.asname or a.name for a in stmt.names)

    # Act
    advertised = set(harness.__all__)
    resolvable = set(harness.__dir__())

    # Assert
    assert advertised == resolvable, "__all__ and the lazy registry disagree"
    assert advertised <= type_checked, (
        f"advertised but absent from the TYPE_CHECKING block: {sorted(advertised - type_checked)}"
    )
    assert all(hasattr(harness, name) for name in advertised)
