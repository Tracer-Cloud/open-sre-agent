"""``AgentSession`` — the public host API for chat and investigation.

Every surface (shell, gateway, embedder, CLI, web) talks to the agent through
this module. Chat turns terminate at ``dispatch_chat_turn``; investigations use
the installed payload runner (wired at process boot). Session lifecycle
(create / resolve / rotate / restore) belongs to
:class:`~core.agent_harness.session.lifecycle.SessionManager`; the session API
sits one layer above and adds env resolution and prompt context.

Construct **one** agent per logical session, then many turns::

    session = AgentSession.start()       # or attach_agent(custom_headless)
    result = session.chat("…")           # turn 1
    follow = session.chat("…")           # turn 2 — same attached agent
    report = session.investigate({…})    # Path-2 (no attached chat agent required)

Custom ports (live gateway sink, REPL)::

    session = AgentSession(SessionConfig(load_env=False))
    session.startup()
    session.attach_agent(build_default_headless_agent(session=…, output=sink, …))
    session.chat(text)                   # reuse the same agent across messages

Gateway chat reuses one ``HeadlessAgent`` per session via ``SessionAgentPool``
(``bind_turn`` each message). Scheduled **one-shots** may use
:meth:`run_headless_turn`; multi-turn loops should keep one agent for the loop.
There is no ``dispatch_message_to_headless_agent`` free function.

Must not import ``surfaces.interactive_shell``. Surfaces inject prompt
context through :class:`SessionConfig`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from core.agent_harness.session import SessionManager

if TYPE_CHECKING:
    from core.agent_harness.investigation_api import AlertInput, InvestigationResult
    from core.agent_harness.ports import OutputSink, PromptContextProvider
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.turns.turn_results import TurnResult


class ChatDispatcher(Protocol):
    """Anything that can run one chat turn for a message (headless or TTY)."""

    def dispatch(self, message: str) -> TurnResult:
        """Run one turn for ``message`` and return its :class:`TurnResult`."""


@dataclass(frozen=True)
class SessionConfig:
    """What a surface hands :class:`AgentSession` to start up.

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


# Scheduled/one-shot runs: a fresh warm session that leaves no persisted task
# registry or open storage behind.
SCHEDULED_RUN_CONFIG = SessionConfig(
    load_env=True,
    hydrate_integrations=True,
    warm_integrations=True,
    persistent_tasks=False,
    open_storage=False,
)


@dataclass(frozen=True)
class SessionStartupResult:
    """Outcome of :meth:`AgentSession.startup`."""

    session: SessionCore
    prompts: PromptContextProvider | None


class AgentSession:
    """Public host API: ``start`` / ``chat`` / ``investigate``.

    Order of startup steps matters: env vars must be resolved before session
    creation (integration hydration/warm may depend on env-provided credentials),
    and context loading is independent of both so it runs last for readability.
    """

    def __init__(self, config: SessionConfig | None = None) -> None:
        self._config = config or SessionConfig()
        self._session_manager = self._config.session_manager or SessionManager()
        self._agent: ChatDispatcher | None = None

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

    def _attach_default_headless(
        self,
        *,
        session: SessionCore,
        output: OutputSink,
        prompts: PromptContextProvider | None,
        message: str | None = None,
        **agent_kwargs: Any,
    ) -> None:
        """Attach the shared default headless port stack (one construction recipe).

        Used by :meth:`start` and :meth:`run_headless_turn`. Custom hosts
        (gateway pool, REPL) still call :func:`build_default_headless_agent` or
        assemble :class:`HeadlessAgent` and :meth:`attach_agent` themselves —
        they must not re-copy this wiring ad hoc.
        """
        from core.agent_harness.turns.default_headless_agent import (
            build_default_headless_agent,
        )

        agent_kwargs.pop("message", None)
        self.attach_agent(
            build_default_headless_agent(
                session=session,
                output=output,
                prompts=prompts,
                message=message,
                **agent_kwargs,
            )
        )

    @classmethod
    def start(
        cls,
        config: SessionConfig | None = None,
        *,
        output: OutputSink | None = None,
        prompts: PromptContextProvider | None = None,
        prepare_session: Callable[[SessionCore], None] | None = None,
        message: str | None = None,
        **agent_kwargs: Any,
    ) -> AgentSession:
        """Return a session that is ready to :meth:`chat`.

        The single headless bootstrap: create the session, run ``prepare_session``,
        resolve the sink and prompt context, and attach the default agent. Build
        it once and dispatch as many turns as the caller needs::

            session = AgentSession.start()
            for prompt in prompts:
                result = session.chat(prompt)

        ``prepare_session`` runs after session create (e.g. pin a project scope)
        and before the agent is built. ``message`` is the first turn's text when
        it is already known, for ports that size themselves to it. Remaining
        keyword arguments reach
        :func:`~core.agent_harness.turns.default_headless_agent.build_default_headless_agent`.
        Surfaces that need their own ports (a live gateway sink, a REPL console)
        build the agent themselves and call :meth:`attach_agent`.
        """
        from core.agent_harness.turns.headless_adapters import BufferOutputSink

        agent_session = cls(config)
        startup = agent_session.startup()
        if prepare_session is not None:
            prepare_session(startup.session)
        agent_session._attach_default_headless(
            session=startup.session,
            output=output if output is not None else BufferOutputSink(),
            prompts=prompts if prompts is not None else startup.prompts,
            message=message,
            **agent_kwargs,
        )
        return agent_session

    @classmethod
    def run_headless_turn(
        cls,
        message: str,
        *,
        config: SessionConfig | None = None,
        output: OutputSink | None = None,
        prepare_session: Callable[[SessionCore], None] | None = None,
        **agent_kwargs: Any,
    ) -> TurnResult:
        """Run exactly one turn for ``message`` on a throwaway session.

        Convenience for scheduled digests and report runners that fire once. A
        loop that runs several turns must call :meth:`start` once and
        :meth:`chat` per turn instead — this rebuilds the session, re-hydrates
        integrations, and discards every warm cache on each call.
        """
        return cls.start(
            config or SCHEDULED_RUN_CONFIG,
            output=output,
            prepare_session=prepare_session,
            message=message,
            **agent_kwargs,
        ).chat(message)

    @property
    def agent(self) -> ChatDispatcher | None:
        """The attached chat dispatcher, if one has been bound."""
        return self._agent

    def startup(self) -> SessionStartupResult:
        """Run env resolution, session bootstrap/resume, and context loading."""
        self.resolve_env_variables()
        session = self.load_or_create_session()
        prompts = self.load_context()
        return SessionStartupResult(session=session, prompts=prompts)

    def attach_agent(self, agent: ChatDispatcher) -> None:
        """Bind a chat dispatcher for :meth:`chat` reuse."""
        self._agent = agent

    def chat(
        self,
        message: str,
        *,
        agent: ChatDispatcher | None = None,
    ) -> TurnResult:
        """Run one chat turn for ``message`` (the public chat verb).

        Prefer :meth:`attach_agent` once, then call this per message::

            session.attach_agent(headless)
            session.chat(text)
        """
        target = agent if agent is not None else self._agent
        if target is None:
            raise RuntimeError(
                "AgentSession.chat requires an attached chat dispatcher "
                "(call attach_agent first, or pass agent=)."
            )
        return target.dispatch(message)

    def investigate(
        self,
        alert: AlertInput,
        *,
        opensre_evaluate: bool = False,
        investigation_metadata: tuple[str, str] | None = None,
    ) -> InvestigationResult:
        """Run a Path-2 investigation and return a typed result.

        Uses the payload runner installed at process boot
        (:func:`core.agent_harness.investigation_api.install_investigation_payload_runner`).
        Does not require an attached chat agent.
        """
        from core.agent_harness.investigation_api import run_installed_investigation_payload

        return run_installed_investigation_payload(
            raw_alert=alert,
            opensre_evaluate=opensre_evaluate,
            investigation_metadata=investigation_metadata,
        )


__all__ = [
    "SCHEDULED_RUN_CONFIG",
    "AgentSession",
    "ChatDispatcher",
    "SessionConfig",
    "SessionStartupResult",
]
