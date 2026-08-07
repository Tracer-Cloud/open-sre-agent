"""The child process the gateway daemon spawns.

A leaf: it imports only ``sys``, so the CLI command and the interactive shell's
``/gateway`` command can both read it without either pulling in the other's
package. Naming it inside ``surfaces.cli.gateway_entry`` instead would make the
shell import the CLI entrypoint, which re-enters the shell's own command
registry mid-import.

It lives under ``surfaces`` rather than ``gateway`` because the surface owns its
composition root; the gateway supervises whatever child it is handed.
"""

from __future__ import annotations

import sys

GATEWAY_ENTRY_ARGV: tuple[str, ...] = (sys.executable, "-m", "surfaces.cli.gateway_entry")

__all__ = ["GATEWAY_ENTRY_ARGV"]
