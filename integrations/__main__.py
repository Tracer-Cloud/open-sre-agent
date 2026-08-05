"""Make the package executable: ``python -m integrations <command> [service]``.

The command surface itself is defined in :mod:`integrations.app`. This module
only launches it, matching ``surfaces/cli/__main__.py``.
"""

from __future__ import annotations

from integrations.app import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
