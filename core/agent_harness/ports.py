"""Ports (structural Protocols) the agentic turn engine talks to.

These are the seams that keep ``agent/`` decoupled from any concrete surface.
The interactive shell implements them as adapters over its ``ReplSession``,
Rich console, tool registry, and grounding caches; the headless adapters in
:mod:`core.agent_harness.headless` implement minimal in-memory versions for API / test runs.

Nothing here imports ``interactive_shell``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.agent_harness.turn_results import ShellTurnResult, ToolCallingTurnResult

if TYPE_CHECKING:
    pass

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
    ) -> str:
        """Stream ``chunks`` to the surface under ``label`` and return the text."""


@runtime_checkable
class SessionStore(Protocol):
    """Mutable per-session state the engine reads and writes.

    ``ReplSession`` satisfies this structurally. The fields mirror what the
    action driver, the three-path engine, and the gather loop touch.
    """

    # --- turn-context snapshot fields (see core.agent_harness.turn_context.TurnContextSource) ---
    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool

    # Read-only here; ``ReplSession`` stores a tuple. A property matches
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
    github_repo_scope: tuple[str, str] | None

    def record(self, kind: str, text: str, *, ok: bool = True) -> None:
        """Append a record of an executed action/turn to the session log."""


@runtime_checkable
class ToolProvider(Protocol):
    """Supplies the action-agent tools and the per-turn tool-event observer."""

    def action_tools(self, *, confirm_fn: ConfirmFn | None, is_tty: bool | None) -> list[Any]:
        """Return the agent tools available for this turn."""

    def observer(self, *, message: str) -> ToolEventObserver:
        """Return a tool-event observer for this turn (e.g. terminal renderer)."""


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

    def cli_reference(self) -> str:
        raise NotImplementedError

    def agents_md(self) -> str:
        raise NotImplementedError

    def investigation_flow(self) -> str:
        raise NotImplementedError

    def environment_block(self) -> str:
        raise NotImplementedError

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


# Bound conversational-answer callable. Returns an opaque LLM-run record (or
# None). The shell binds session/console/grounding; headless binds a simple
# core-LLM call.
AnswerAgent = Callable[..., Any]

# Bound evidence-gather callable: ``gather(text, *, is_tty) -> str | None``.
EvidenceGatherer = Callable[..., "str | None"]

# Bound action tool-calling driver:
# ``execute_actions(text, *, confirm_fn, is_tty, turn_ctx) -> ToolCallingTurnResult``.
ExecuteActions = Callable[..., ToolCallingTurnResult]


@runtime_checkable
class TurnAccounting(Protocol):
    """Records analytics/telemetry for a turn and finalizes the result."""

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        raise NotImplementedError

    def finalize(self, result: ShellTurnResult) -> ShellTurnResult:
        raise NotImplementedError


__all__ = [
    "AnswerAgent",
    "ConfirmFn",
    "ErrorReporter",
    "EvidenceGatherer",
    "ExecuteActions",
    "OutputSink",
    "PromptContextProvider",
    "ReasoningClientProvider",
    "RunRecordFactory",
    "SessionStore",
    "ToolEventObserver",
    "ToolProvider",
    "TurnAccounting",
]
