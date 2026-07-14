"""Register scheduled agent digests (Sentry + GitHub PR sweep)."""

from __future__ import annotations

from integrations.scheduled_agent_bootstrap import install as install_scheduled_agent


def install() -> None:
    """Bind the multiplexed scheduled agent runner (backward-compatible entry)."""
    install_scheduled_agent()


__all__ = ["install"]
