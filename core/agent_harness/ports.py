"""Ports (structural Protocols) the agentic turn engine talks to.

These are the seams that keep ``agent/`` decoupled from any concrete surface.
The interactive shell implements them as adapters over its ``Session``,
Rich console, tool registry, and grounding caches; the headless adapters in
:mod:`core.agent_harness.turns.headless_dispatch` implement minimal in-memory versions for API / test runs.

Nothing here imports ``interactive_shell``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

# A tool-loop event callback: ``(kind, data)`` where kind is e.g. "tool_start".
ToolEventObserver = Callable[[str, dict[str, Any]], None]

# Confirmation prompt: given a summary, return the user's response string.
ConfirmFn = Callable[[str], str]


@runtime_checkable
class OutputSink(Protocol):
    """Where the engine renders user-facing output."""

    def print(self, message: str = "") -> None:
        """Print one line of (markup-bearing) text."""

    def render_response_header(self, label: str) -> None:
        """Render the assistant response header (e.g. a labelled rule)."""

    def render_error(self, message: str) -> None:
        """Render an error/notice line."""

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        """Stream ``chunks`` to the surface under ``label`` and return the text.

        When ``defer_want_me_to_closer`` is true, surfaces may hold the Want-me-to
        closer until ``finish_streamed_response`` (optional sink method; gather
        normalize path).
        """


@runtime_checkable
class SessionStore(Protocol):
    """Mutable per-session state the engine reads and writes.

    ``Session`` satisfies this structurally. The fields mirror what the
    action driver, the three-path engine, and the gather loop touch.
    """

    # --- turn-context snapshot fields ---
    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool

    # Read-only here; ``Session`` stores a tuple. A property matches
    # covariantly, so any concrete ``Sequence[str]`` implementation satisfies it.
    @property
    def configured_integrations(self) -> Sequence[str]:
        raise NotImplementedError

    last_state: dict[str, Any] | None
    last_synthetic_observation_path: str | None
    reasoning_effort: Any | None

    # --- turn execution state ---
    history: list[dict[str, Any]]
    last_command_observation: str | None
    session_id: str

    # --- gather caches ---
    resolved_integrations_cache: dict[str, Any] | None
    vcs_repo_scopes: dict[str, tuple[str, ...]]

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        """Append a record of an executed action/turn to the session log."""


@runtime_checkable
class SessionBindable(Protocol):
    """Port that can retarget a session when the agent is reused across turns.

    Pooled / multi-turn hosts call :meth:`bind_session` when
    ``SessionManager.resolve`` returns a fresh session object for the same id.
    Ports that ignore session identity need not implement this; ``HeadlessAgent``
    only invokes it when the port structurally matches.
    """

    def bind_session(self, session: SessionStore) -> None:
        """Point this port at ``session`` (same logical session, new object)."""


@runtime_checkable
class ConsoleBindable(Protocol):
    """Tool port that can retarget the turn console (cancel / TTY observers).

    Gateway binds a per-turn ``CancelConsole`` so ``cancel_requested`` tracks
    the shared ``sink.turn_cancel`` Event for that message.
    """

    def bind_console(self, console: Any) -> None:
        """Point tool UI / cancel probes at ``console`` for this turn."""


@runtime_checkable
class OutputBindable(Protocol):
    """Port that holds an :class:`OutputSink` and can retarget it across turns.

    ``HeadlessAgent.bind_turn(output=…)`` updates the agent's sink and must
    retarget every port that cached the previous sink (e.g. reasoning error
    rendering). Gateway usually keeps a stable ``LiveOutputSink`` and rebinds
    the outer transport sink via ``LiveOutputSink.bind`` — that path does not
    need ``bind_turn(output=)``. Hosts that swap the ``OutputSink`` object
    itself must pass ``output=`` so :class:`OutputBindable` ports follow.
    """

    def bind_output(self, output: OutputSink) -> None:
        """Point this port at ``output`` for the current turn."""


@runtime_checkable
class ToolProvider(Protocol):
    """Supplies the action-agent tools and the per-turn tool-event observer."""

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Return the agent tools available for this turn.

        When ``resolved_integrations`` is supplied it is the turn's single
        resolved-integration view (from ``TurnSnapshot``); the provider builds
        tools from it instead of resolving again, so tools and the prompt agree.
        """

    def tool_resources(self) -> dict[str, Any]:
        """Return non-serializable resources for tools that opt into runtime context."""

    def observer(self, *, message: str) -> ToolEventObserver:
        """Return a tool-event observer for this turn (e.g. terminal renderer)."""


@runtime_checkable
class ToolRegistry(Protocol):
    """Resolves the registered tools available to a named surface."""

    def tools_for_surface(self, surface: str) -> list[Any]:
        """Return the registered tools for ``surface`` (e.g. ``"action"``)."""

    def tool_map_for_surface(self, surface: str) -> dict[str, Any]:
        """Return the registered tools for ``surface`` keyed by tool name."""


@runtime_checkable
class ErrorReporter(Protocol):
    """Reports caught exceptions (telemetry / logging)."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        raise NotImplementedError


@runtime_checkable
class PromptContextProvider(Protocol):
    """Supplies grounding text for the conversational assistant prompt.

    The grounding corpora (CLI reference, repo map, docs, investigation-flow,
    environment) are surface/repo content; the shell adapter wires its grounding
    caches, the headless adapter returns empty strings.
    """

    def surface(self) -> str:
        """Which surface this turn runs on; defaults to the interactive shell."""
        return "interactive_shell"

    def cli_reference(self) -> str:
        raise NotImplementedError

    def agents_md(self) -> str:
        raise NotImplementedError

    def docs(self, query: str) -> str:
        raise NotImplementedError

    def investigation_flow(self) -> str:
        raise NotImplementedError

    def runtime_facts(self) -> Mapping[str, Any]:
        """Runtime facts for this turn: session metadata plus fresh live values."""
        raise NotImplementedError

    def environment_block(self, runtime: Mapping[str, Any] | None = None) -> str:
        """Static environment block; ``runtime`` reuses the turn's capture."""
        raise NotImplementedError

    def long_term_memory(self) -> str:
        raise NotImplementedError

    def setup_state(self) -> str:
        """The operator's connected integrations and schedules, as a fact block."""

    def suggested_synthetic_prompt(self) -> str:
        raise NotImplementedError

    def log_diagnostics(self, reason: str) -> None:
        raise NotImplementedError


@runtime_checkable
class ReasoningClientProvider(Protocol):
    """Provides the streaming reasoning LLM client for the assistant answer."""

    def get(self) -> Any | None:
        raise NotImplementedError


@runtime_checkable
class RunRecordFactory(Protocol):
    """Builds the opaque per-answer LLM-run record (telemetry) from raw inputs."""

    def build(self, *, client: Any, prompt: str, response_text: str, started: float) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class AnswerRequest:
    """Per-turn inputs for the direct-answer (no tools) path.

    Surface-bound ports (session, output, prompts, …) live on the caller;
    only what varies per turn goes here. Confirm/TTY stay on the action path —
    the direct answer never prompts or branches on them.
    """

    tool_observation: str | None = None
    tool_observation_on_screen: bool = True
    handoff_contents: tuple[str, ...] = ()
    # ``Any`` rather than ``TurnPlan``: that type imports ``SessionStore`` from
    # here, so naming it — even under ``TYPE_CHECKING`` — closes an import cycle
    # this repo's check rejects.
    turn_plan: Any = None
    # Gather answers defer Want-me-to paint until the harness normalizes the
    # closer (dual paste/integrations menus must not be what the user sees).
    defer_want_me_to_closer: bool = False


class StreamAnswerFn(Protocol):
    """Bound direct-answer callable (no tools) handed to ``run_turn``."""

    def __call__(self, text: str, request: AnswerRequest) -> Any:
        """Stream one grounded answer; return the LLM-run record or None."""


class EvidenceGatherer(Protocol):
    """Bound evidence-gather callable handed to ``run_turn``."""

    def __call__(self, text: str, *, turn_plan: Any = None) -> str | None:
        """Gather read-only evidence for ``text``, or return None."""


class ExecuteActions(Protocol):
    """Bound action tool-calling driver handed to ``run_turn``."""

    def __call__(
        self,
        text: str,
        *,
        confirm_fn: ConfirmFn | None = None,
        is_tty: bool | None = None,
        turn_plan: Any = None,
    ) -> ToolCallingTurnResult:
        """Run the action tool-calling turn for ``text``."""


@runtime_checkable
class TurnAccounting(Protocol):
    """Records analytics/telemetry for a turn and finalizes the result."""

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        raise NotImplementedError

    def finalize(self, result: TurnResult) -> TurnResult:
        raise NotImplementedError


__all__ = [
    "AnswerRequest",
    "StreamAnswerFn",
    "ConfirmFn",
    "ConsoleBindable",
    "ErrorReporter",
    "EvidenceGatherer",
    "ExecuteActions",
    "OutputSink",
    "PromptContextProvider",
    "ReasoningClientProvider",
    "RunRecordFactory",
    "SessionBindable",
    "SessionStore",
    "ToolEventObserver",
    "ToolProvider",
    "ToolRegistry",
    "TurnAccounting",
]
