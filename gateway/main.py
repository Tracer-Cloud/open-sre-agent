"""Process entry for the OpenSRE messaging gateway.

Started by the daemon as ``python -m gateway.main`` (also
``opensre gateway start`` / ``opensre gateway start --foreground``).
Delegates to :func:`gateway.runtime.manager.main`.
"""

from __future__ import annotations

from app.entrypoints.gateway import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
