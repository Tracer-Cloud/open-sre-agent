"""``AgentHarness`` — consolidated agent startup for every surface.

Startup behavior for a turn-driving agent was previously scattered across
surfaces: the interactive shell, the gateway, and the investigation pipeline
each resolved integrations, loaded prior session history, and read env vars
in their own way (see ``AGENTS.md`` "Large multi-surface refactors" and
https://github.com/Tracer-Cloud/opensre/issues/3359). Session lifecycle
itself — create / resolve / rotate / restore / integration hydration — is
already owned by :class:`~core.agent_harness.session.manager.SessionManager`
(issue #3357). ``AgentHarness`` sits one layer above that: it is the single
call surfaces make to get a bootstrapped session *and* the other two startup
concerns ``SessionManager`` does not own — env resolution and grounding
context — in one call, instead of each surface sequencing
``load_dotenv`` + ``SessionManager`` + its own prompt-context wiring itself.

The four responsibilities from #3359:

1. **Resolving integrations** — delegated to ``SessionManager``'s bootstrap
   (``hydrate_integrations`` / ``warm_integrations`` on :meth:`HarnessConfig`),
   plus :meth:`AgentHarness.resolve_integrations` for on-demand full resolution
   once a session exists (thin wrapper over
   :meth:`~core.agent_harness.session.state.Session.get_integrations`).
2. **Loading context** — the surface's injected
   :class:`~core.agent_harness.ports.PromptContextProvider`, if any. The
   harness does not render grounding text itself (``ports.py`` already models
   that as a per-surface concern); this step exists so surfaces get it from
   the same call as everything else.
3. **Loading previous session history from disk** — ``session_id`` +
   ``SessionManager.resolve()``, which loads the persisted session and
   restores its conversation context.
4. **Resolving env variables** — a single ``load_dotenv`` call.

This module must not import ``interactive_shell`` / ``surfaces.interactive_shell``
(enforced by ``tests/core/agent/test_import_boundaries.py``) — surfaces build a
:class:`HarnessConfig` from their own prompt-context provider and hand it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from core.agent_harness.session import SessionManager

if TYPE_CHECKING:
    from core.agent_harness.ports import PromptContextProvider
    from core.agent_harness.session.state import Session


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

    session: Session
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

    def resolve_env_variables(self) -> None:
        """Load a local ``.env`` file into the process environment, once.

        ``override=False`` matches every existing call site (gateway,
        ``integrations/__main__.py``): a variable already set in the real
        environment wins over the ``.env`` file.
        """
        if self._config.load_env:
            load_dotenv(override=False)

    def load_or_create_session(self) -> Session:
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

    def resolve_integrations(self, session: Session) -> dict[str, Any]:
        """Return resolved integration configs for ``session``."""
        from core.agent import Agent

        return Agent.resolve_integrations(session)

    def load_context(self) -> PromptContextProvider | None:
        """Return the surface's grounding-context provider, if any."""
        return self._config.prompts

    def startup(self) -> HarnessStartupResult:
        """Run env resolution, session bootstrap/resume, and context loading."""
        self.resolve_env_variables()
        session = self.load_or_create_session()
        prompts = self.load_context()
        return HarnessStartupResult(session=session, prompts=prompts)


__all__ = ["AgentHarness", "HarnessConfig", "HarnessStartupResult"]
