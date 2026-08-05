"""Make the package executable: ``python -m surfaces.cli.wizard``.

The wizard entry itself is defined in :mod:`surfaces.cli.wizard.app`. This
module only launches it, matching ``surfaces/cli/__main__.py``.
"""

from __future__ import annotations

from surfaces.cli.wizard.app import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
