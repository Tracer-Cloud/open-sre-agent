"""CLI helpers.

Importing this package must stay cheap: ``python -m surfaces.cli`` loads it
before ``--help`` / ``--version``. Public names resolve on first access.
"""

from __future__ import annotations

from typing import Any

__all__ = ["write_json"]


def __getattr__(name: str) -> Any:
    if name == "write_json":
        from surfaces.cli.args import write_json

        return write_json
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
