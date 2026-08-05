"""``AgentHarness`` — one-call agent startup shared by every surface.

Before a surface can drive agent turns it needs three things set up, in order:
env vars loaded, a session created or resumed, and the prompt context loaded.
``AgentHarness`` runs those steps in one call so the shell, gateway, and
investigation pipeline don't each wire them up their own way. Session lifecycle
(create / resolve / rotate / restore) belongs to
:class:`~core.agent_harness.session.lifecycle.SessionManager`; the harness sits
one layer above and adds env resolution and prompt context.

Headless turns::

    harness = AgentHarness(...)
    harness.attach_agent(headless)  # or pass agent= on each call
    harness.dispatch_message("investigate the spike")

Must not import ``surfaces.interactive_shell`` (enforced by
``tests/core/agent/test_import_boundaries.py``). Surfaces inject prompt
context through :class:`HarnessConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.agent_harness.session import SessionManager

if TYPE_CHECKING:
    from core.agent_harness.ports import PromptContextProvider
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.turns.headless_dispatch import HeadlessAgent
    from core.agent_harness.turns.turn_results import TurnResult


@dataclass(frozen=True)
class HarnessConfig:
    """What a surface hands :class:`AgentHarness` to start up an agent.

    Every field is optional so a surface only opts into the behavior it
    needs: a fresh gateway turn has nothing to resume (``session_id=None``);
    a headless action-only turn has no grounded context (``prompts=None``).
    """

    session_id: str | None = None
    prompts: PromptContextProvider | None = None
    load_env: bool = True
    hydrate_integrations: bool = True
    # None defers to SessionManager's own per-operation default: eager warm on
    # resolve() (a resumed session needs tools ready immediately), lazy on
    # create() (a fresh session can warm on first turn).
    warm_integrations: bool | None = None
    persistent_tasks: bool = True
    open_storage: bool = True
    session_manager: SessionManager | None = None


@dataclass(frozen=True)
class HarnessStartupResult:
    """Outcome of :meth:`AgentHarness.startup`."""

    session: SessionCore
    prompts: PromptContextProvider | None


class AgentHarness:
    """Runs the startup steps every surface needs, in a fixed order.

    Order matters: env vars must be resolved before session creation
    (integration hydration/warm may depend on env-provided credentials), and
    context loading is independent of both so it runs last for readability,
    not because anything depends on it running after.
    """

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self._config = config or HarnessConfig()
        self._session_manager = self._config.session_manager or SessionManager()
        self._agent: HeadlessAgent | None = None

    def resolve_env_variables(self) -> None:
        """Load local OpenSRE env defaults into the process, once.

        Delegates to :func:`config.local_env.bootstrap_opensre_env_once` so CLI,
        gateway, and web share one path (project ``.env`` + wizard defaults).
        ``override=False``: a variable already set in the real environment wins.
        """
        if self._config.load_env:
            from config.local_env import bootstrap_opensre_env_once

            bootstrap_opensre_env_once(override=False)

    def load_or_create_session(self) -> SessionCore:
        """Resume a persisted session if ``session_id`` was given, else create one.

        Delegates entirely to :class:`SessionManager` — this method does not
        duplicate its bootstrap/restore logic, it just picks which lifecycle
        call to make based on whether the surface is resuming.
        """
        manager = self._session_manager
        if self._config.session_id:
            # SessionManager.resolve()'s own default is True: a resumed
            # session needs tools ready immediately.
            warm = (
                True if self._config.warm_integrations is None else self._config.warm_integrations
            )
            return manager.resolve(
                self._config.session_id,
                hydrate_integrations=self._config.hydrate_integrations,
                warm_integrations=warm,
                persistent_tasks=self._config.persistent_tasks,
            )
        # SessionManager.create()'s own default is False: a fresh session can
        # warm lazily on first turn.
        warm = False if self._config.warm_integrations is None else self._config.warm_integrations
        return manager.create(
            hydrate_integrations=self._config.hydrate_integrations,
            warm_integrations=warm,
            persistent_tasks=self._config.persistent_tasks,
            open_storage=self._config.open_storage,
        )

    def resolve_integrations(self, session: SessionCore) -> dict[str, Any]:
        """Return resolved integration configs for ``session``."""
        from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations

        return resolve_and_cache_integrations(session)

    def load_context(self) -> PromptContextProvider | None:
        """Return the surface's grounding-context provider, if any."""
        return self._config.prompts

    @classmethod
    def start(cls, config: HarnessConfig | None = None) -> AgentHarness:
        """Return a harness that is ready to :meth:`dispatch_message`.

        Runs startup and attaches a default agent, so the common case is two
        lines rather than assembling a console, logger, sink and factory call::

            harness = AgentHarness.start()
            result = harness.dispatch_message("why is checkout-api slow?")

        Surfaces that need their own ports (a live gateway sink, a REPL console)
        still build the agent themselves and call :meth:`attach_agent`.
        """
        from core.agent_harness.turns.default_headless_agent import (
            build_default_headless_agent,
        )
        from core.agent_harness.turns.headless_adapters import BufferOutputSink

        harness = cls(config)
        startup = harness.startup()
        harness.attach_agent(
            build_default_headless_agent(
                session=startup.session,
                output=BufferOutputSink(),
                # A caller's HarnessConfig.prompts, else the built-in grounding context.
                prompts=startup.prompts,
            )
        )
        return harness

    @property
    def agent(self) -> HeadlessAgent | None:
        """The attached agent, if one has been bound."""
        return self._agent

    def startup(self) -> HarnessStartupResult:
        """Run env resolution, session bootstrap/resume, and context loading."""
        self.resolve_env_variables()
        session = self.load_or_create_session()
        prompts = self.load_context()
        return HarnessStartupResult(session=session, prompts=prompts)

    def attach_agent(self, agent: HeadlessAgent) -> None:
        """Bind a :class:`HeadlessAgent` for :meth:`dispatch_message` reuse."""
        self._agent = agent

    def dispatch_message(
        self,
        message: str,
        *,
        agent: HeadlessAgent | None = None,
    ) -> TurnResult:
        """Run one headless turn for ``message``.

        Prefer :meth:`attach_agent` once, then call this per message::

            harness.attach_agent(headless)
            harness.dispatch_message(text)
        """
        target = agent if agent is not None else self._agent
        if target is None:
            raise RuntimeError(
                "AgentHarness.dispatch_message requires an attached HeadlessAgent "
                "(call attach_agent first, or pass agent=)."
            )
        return target.dispatch(message)


__all__ = ["AgentHarness", "HarnessConfig", "HarnessStartupResult"]
