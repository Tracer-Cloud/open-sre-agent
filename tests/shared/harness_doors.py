"""The harness's public import doors, shared by the border tests and the API pin.

One list so the border tests (which allow these modules) and
``tests/core/agent_harness/test_public_api.py`` (which pins their contents)
cannot drift apart: a role added to one is a role added to both.
"""

from __future__ import annotations

SPI_ROLES: frozenset[str] = frozenset(
    {
        "session_goal",
        "session_flags",
        "cancel",
        "accounting",
        "prompt_chrome",
        "integrations",
        "grounding",
        "defaults",
    }
)

#: Every module a host may import the harness through. Exact names — no prefix
#: rule, so a future internal module under ``spi/`` is not public by accident.
PUBLIC_DOORS: frozenset[str] = frozenset(
    {"core.agent_harness", "core.agent_harness.ports", "core.agent_harness.runtime"}
    | {f"core.agent_harness.spi.{role}" for role in SPI_ROLES}
)

__all__ = ["PUBLIC_DOORS", "SPI_ROLES"]
