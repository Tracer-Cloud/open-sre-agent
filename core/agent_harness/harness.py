"""``AgentHarness`` — one-call agent startup shared by every surface.

Before a surface can drive agent turns it needs three things set up, in order:
env vars loaded, a session created or resumed, and the prompt context loaded.
``AgentHarness`` runs those steps in one call so the shell, gateway, and
investigation pipeline don't each wire them up their own way. Session lifecycle
(create / resolve / rotate / restore) belongs to
:class:`~core.agent_harness.session.lifecycle.SessionManager`; the harness sits
one layer above and adds env resolution and prompt context.

Must not import ``surfaces.interactive_shell`` (enforced by
``tests/core/agent/test_import_boundaries.py``): surfaces pass their own
prompt-context provider in through :class:`HarnessConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from core.agent_harness.session import SessionManager

if TYPE_CHECKING:
    from core.agent_harness.ports import PromptContextProvider
    from core.agent_harness.session.session_core import SessionCore


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

    def resolve_env_variables(self) -> None:
        """Load a local ``.env`` file into the process environment, once.

        ``override=False`` matches every existing call site (gateway,
        ``integrations/__main__.py``): a variable already set in the real
        environment wins over the ``.env`` file.
        """
        if self._config.load_env:
            load_dotenv(override=False)

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

    def startup(self) -> HarnessStartupResult:
        """Run env resolution, session bootstrap/resume, and context loading."""
        self.resolve_env_variables()
        session = self.load_or_create_session()
        prompts = self.load_context()
        return HarnessStartupResult(session=session, prompts=prompts)


__all__ = ["AgentHarness", "HarnessConfig", "HarnessStartupResult"]
