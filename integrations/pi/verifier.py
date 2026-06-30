"""Verification for the Pi coding integration (binary installed + authenticated).

Reuses the ``llm_cli`` Pi adapter's ``detect()`` so install/auth detection stays
in one place (provider role and coding-tool role share the same binary + creds).
"""

from __future__ import annotations

from integrations.llm_cli.pi_cli import PiAdapter


def verify_pi_coding() -> tuple[bool, str]:
    """Return ``(available, detail)`` for the Pi coding tool.

    ``available`` is True only when the ``pi`` binary is installed and auth is not
    explicitly missing (``logged_in`` is True or unclear). Auth ``None`` (unclear)
    is treated as available; the actual run surfaces a clear error if it fails.
    """
    probe = PiAdapter().detect()
    if not probe.installed:
        return False, probe.detail
    if probe.logged_in is False:
        return False, probe.detail
    return True, probe.detail
