"""Report which execution capabilities this environment actually grants.

The agent's prompt tells it that it can run Python, read files, and reach the
network. When the environment withholds one, the shortfall surfaces mid-turn as
an evasive answer instead of a clear failure. Probing once at startup turns
"the agent seems oddly limited" into a warning naming the missing capability.

This is not the same question as ``config.runtime_metadata.probes``, which
reports whether a binary is on ``PATH``: a binary can be present and still be
unusable. Here the question is whether the capability *works*.

Every probe is cheap, local, and non-fatal. A diagnostic that raises during
startup is worse than no diagnostic, so failures are recorded as unavailable
with the reason attached.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Capability(StrEnum):
    """A thing the agent is told it can do."""

    PYTHON = "python execution"
    SHELL = "shell commands"
    FILE_READ = "file reading"
    NETWORK = "network requests"


@dataclass(frozen=True)
class CapabilityStatus:
    """Whether one capability is usable, and why not when it is not."""

    capability: Capability
    available: bool
    detail: str


def _python_available() -> bool:
    """True when the sandbox can execute generated Python."""
    from platform.sandbox.runner import run_python_sandbox

    result = run_python_sandbox("print(1 + 1)")
    return "2" in str(getattr(result, "stdout", ""))


def _shell_available() -> bool:
    """True when a shell interpreter exists to run generated scripts."""
    return bool(shutil.which("bash") or shutil.which("sh"))


def _file_read_available() -> bool:
    """True when the process can read its own working tree."""
    return Path.cwd().is_dir() and any(Path.cwd().iterdir()) is not None


def _network_available() -> bool:
    """True when outbound requests are not blocked by policy.

    Deliberately does **not** open a socket: startup must not depend on an
    external host being reachable, and a probe that hangs on a firewalled
    network is worse than the gap it reports. Only the local policy that would
    refuse the call is inspected.
    """
    from platform.sandbox.runner import run_python_sandbox

    result = run_python_sandbox("import socket", allow_network=True)
    return "Network access is not permitted" not in str(getattr(result, "stderr", ""))


#: Probes are held by name, not by reference, so they resolve through the module
#: at call time — a test substituting one is honoured.
_PROBES: tuple[tuple[Capability, str], ...] = (
    (Capability.PYTHON, "_python_available"),
    (Capability.SHELL, "_shell_available"),
    (Capability.FILE_READ, "_file_read_available"),
    (Capability.NETWORK, "_network_available"),
)


def probe_capabilities() -> dict[Capability, CapabilityStatus]:
    """Check every capability. Never raises — a failed probe reports unavailable."""
    results: dict[Capability, CapabilityStatus] = {}
    for capability, probe_name in _PROBES:
        try:
            available = bool(globals()[probe_name]())
            detail = "" if available else "probe returned unavailable"
        except Exception as err:
            available, detail = False, f"{type(err).__name__}: {err}"
        results[capability] = CapabilityStatus(
            capability=capability, available=available, detail=detail
        )
    return results


def unavailable_capability_warnings(
    results: dict[Capability, CapabilityStatus] | None = None,
) -> list[str]:
    """One human-readable warning per capability this environment withholds."""
    checked = probe_capabilities() if results is None else results
    return [
        f"{status.capability} is unavailable in this environment"
        + (f" ({status.detail})" if status.detail else "")
        + " — the agent will not be able to use it."
        for status in checked.values()
        if not status.available
    ]


__all__ = [
    "Capability",
    "CapabilityStatus",
    "probe_capabilities",
    "unavailable_capability_warnings",
]
