"""Make the package executable: ``python -m surfaces.cli``.

The CLI itself is defined in :mod:`surfaces.cli.app`. This module only launches
it, matching ``gateway/main.py``.
"""

from __future__ import annotations

from surfaces.cli.app import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
