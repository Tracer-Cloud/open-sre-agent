"""Quickstart wizard entrypoints.

Public callers should import directly from the submodule that owns the
behaviour they need (``cli.wizard.flow`` for the top-level
``run_wizard`` entry, ``cli.wizard.store`` for local-config helpers,
etc.). This ``__init__`` no longer re-exports anything so the package
load is side-effect-free and the legacy ``cli.wizard ↔ cli.wizard.flow``
import cycle is gone.
"""

from __future__ import annotations
