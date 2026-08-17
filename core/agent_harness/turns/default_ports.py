"""The product's default port family for one session, and the agent built on it.

**This is the single headless construction seam.** Gateway ``SessionAgentPool``,
the REPL and ``AgentSession.start`` all build through :meth:`DefaultPorts.agent`
— they are not parallel stacks. A host varies the agent through its ports: it
passes its own :class:`~core.agent_harness.ports.ToolProvider` (usually a
configured :class:`~core.agent_harness.tools.tool_provider.DefaultToolProvider`),
prompt provider and gather ports; reasoning, run records and error reporting
are this family's and are not host-configurable.

Per-turn values (``confirm_fn``, ``is_tty``, hooks, accounting, stage overrides)
are bound on the agent with :meth:`HeadlessAgent.bind_turn` /
:meth:`HeadlessAgent.bind_stages`.

Process boot (``configure_process``) is a **different layer** — adapters and
env — and does not build agents.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from io import StringIO
from typing import TYPE_CHECKING, Any

from rich.console import Console

from core.agent_harness.accounting.run_record import DefaultRunRecordFactory
from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.ports import (
    ErrorReporter,
    LlmFactory,
    OutputSink,
    PromptContextProvider,
    ReasoningClientProvider,
    RunRecordFactory,
    ToolProvider,
)
from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.default_reasoning_client import DefaultReasoningClientProvider
from core.agent_harness.turns.gather_ports import GATHER_DISABLED, GatherPorts
from core.agent_harness.turns.headless_dispatch import HeadlessAgent

if TYPE_CHECKING:
    from core.agent_harness.session.session_core import SessionCore


@dataclass(frozen=True)
class DefaultPorts:
    """The default adapters for ``session`` streaming to ``output``.

    ``console`` and ``logger`` feed only the defaults (tool rendering, tool
    action log, error reporting) and default to headless-safe instances;
    ``surface`` selects the prompt profile of the default prompt provider;
    ``error_reporter`` replaces the default reporter for every stage.
    """

    session: SessionCore
    output: OutputSink
    console: Any | None = None
    logger: logging.Logger | None = None
    surface: str | None = None
    #: A host's reporter for swallowed exceptions (the REPL adds Sentry); default logs.
    error_reporter: ErrorReporter | None = None

    @cached_property
    def _console(self) -> Any:
        return (
            self.console
            if self.console is not None
            else Console(force_terminal=False, file=StringIO())
        )

    @cached_property
    def _logger(self) -> logging.Logger:
        return self.logger if self.logger is not None else logging.getLogger("opensre")

    @cached_property
    def _error_reporter(self) -> ErrorReporter:
        return (
            self.error_reporter
            if self.error_reporter is not None
            else DefaultErrorReporter(self._logger)
        )

    def tools(self) -> ToolProvider:
        """A bare :class:`DefaultToolProvider`; hosts pass their own configured one to :meth:`agent`."""
        return DefaultToolProvider(self.session, self._console, tool_action_logger=self._logger)

    def prompts(self) -> PromptContextProvider:
        if self.surface is not None:
            return DefaultPromptContextProvider(self.session, surface=self.surface)
        return DefaultPromptContextProvider(self.session)

    def reasoning(self) -> ReasoningClientProvider:
        return DefaultReasoningClientProvider(
            output=self.output, error_reporter=self._error_reporter, session=self.session
        )

    def run_factory(self) -> RunRecordFactory:
        return DefaultRunRecordFactory(self.session)

    def agent(
        self,
        *,
        tools: ToolProvider | None = None,
        prompts: PromptContextProvider | None = None,
        gather: GatherPorts | None = None,
        llm_factory: LlmFactory | None = None,
    ) -> HeadlessAgent:
        """The agent on this family; each port a host passes replaces that default.

        ``gather`` defaults to disabled because the evidence pass reaches live
        integrations. ``is not None`` rather than ``or``: a provider defining
        ``__bool__`` must not be silently replaced.
        """
        return HeadlessAgent(
            session=self.session,
            output=self.output,
            tools=tools if tools is not None else self.tools(),
            prompts=prompts if prompts is not None else self.prompts(),
            reasoning=self.reasoning(),
            run_factory=self.run_factory(),
            error_reporter=self._error_reporter,
            gather=gather if gather is not None else GATHER_DISABLED,
            llm_factory=llm_factory,
        )


__all__ = ["DefaultPorts"]
