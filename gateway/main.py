"""Process entry for the OpenSRE messaging gateway.

Prefer ``python -m surfaces.cli.gateway_entry`` (what the daemon spawns): that
composition root wires headless slash ports. This module keeps
``python -m gateway.main`` working for callers that only need the manager
boot path without the surfaces glue.
"""

from __future__ import annotations

from gateway.runtime.manager import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
