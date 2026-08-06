"""Gateway process boot — thin wrapper around :func:`configure_process`.

Shared order and steps live in :mod:`bootstrap.process`. This module
keeps a stable import path for tests during the migration. It does **not**
return an :class:`AgentHarness` — ``GatewayManager`` never used that value
and builds turn agents via :class:`SessionAgentPool` instead.
"""

from __future__ import annotations

import logging

from bootstrap.process import GATEWAY_PROFILE, configure_process


def run(logger: logging.Logger) -> None:
    """Run shared gateway process setup (``GATEWAY_PROFILE``)."""
    configure_process(GATEWAY_PROFILE, logger=logger)


__all__ = ["run"]
