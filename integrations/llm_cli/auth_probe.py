"""Integrations-backed CLI auth prober that fills the config port.

Maps a registered CLI adapter's live ``detect()`` probe onto the config-tier
:class:`config.llm_auth.cli_probe.CliAuthProbe`, so config can report CLI auth
status without importing this package.
"""

from __future__ import annotations

from config.llm_auth.cli_probe import CliAuthProbe


def probe_cli_auth(provider: str) -> CliAuthProbe | None:
    """Probe a CLI provider's local install/auth via its registered adapter."""
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration(provider)
    if reg is None:
        return None
    result = reg.adapter_factory().detect()
    return CliAuthProbe(
        installed=result.installed,
        logged_in=result.logged_in,
        detail=result.detail,
    )


__all__ = ["probe_cli_auth"]
