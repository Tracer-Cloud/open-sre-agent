"""Shared UI theme constants (color hex codes).

Lives at the top of ``app/`` rather than under ``app/cli/`` because
core modules (e.g. ``app/agent/stages/publish_findings/renderers/
terminal.py``) need the same color values without depending on the
CLI layer. The CLI's theme module re-exports these so existing
``from app.cli.interactive_shell.ui.theme import BRAND, …`` imports
keep working — the hex values stay in one place.
"""

from __future__ import annotations

HIGHLIGHT = "#B9EDAF"
BRAND = "#66A17D"
DIM = "#444444"
WARNING = "#CEA25C"

__all__ = ["BRAND", "DIM", "HIGHLIGHT", "WARNING"]
