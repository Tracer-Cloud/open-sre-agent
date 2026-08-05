"""Everything the gateway process does once, before any component starts.

Distinct from :mod:`gateway.runtime.bootstrap`, which registers harness
adapters and scheduler runners — one step of the sequence below.

``start_gateway`` is a lifecycle method: assemble the turn handler, start the
components, own the signals. The process-level setup underneath it — resolving
env vars, registering harness adapters, error reporting, capability probes,
warming the LLM client graph — is not gateway logic and does not belong there.

Order is load-bearing:

1. **env variables** first, because adapter and integration hydration can depend
   on credentials supplied through the environment;
2. **harness adapters** next, so the tool and integration registries are
   populated;
3. **LLM clients** last, so the preloaded snapshot reflects the registered set.

Sentry and the capability probes sit between them: both are diagnostics, and the
capability warnings are deliberately emitted at boot so a withheld capability
reads as a startup warning rather than a confusing mid-turn answer.
"""

from __future__ import annotations

import logging

from core.agent_harness.harness import AgentHarness, HarnessConfig
from core.llm.internal.preload import preload_llm_clients
from gateway.runtime.bootstrap import install_runtime
from platform.observability.errors.sentry import init_sentry
from platform.sandbox.capabilities import boot_capability_warnings


def _build_harness() -> AgentHarness:
    """The gateway's harness: storage stays closed; each turn resolves its own session."""
    return AgentHarness(HarnessConfig(open_storage=False))


def run(logger: logging.Logger) -> AgentHarness:
    """Run the shared process setup and return the harness the gateway will use.

    Safe to call once per process, before any component starts.
    """
    harness = _build_harness()
    harness.resolve_env_variables()

    # Registered here rather than via the shell boundary: gateway must not import
    # surfaces (peer packages).
    install_runtime(harness_adapters=True, scheduler_runners=False)

    # Env-gated (OPENSRE_NO_TELEMETRY / DO_NOT_TRACK / missing DSN) — free when off.
    init_sentry(entrypoint="gateway")

    for warning in boot_capability_warnings():
        logger.warning("[gateway] capability: %s", warning)

    # One snapshot at boot, so a code change mid-process cannot leave a
    # mixed-version client graph.
    preload_llm_clients()
    return harness


__all__ = ["run"]
