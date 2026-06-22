"""Compatibility alias for interactive shell data-store context helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.data_store import context as _context

sys.modules[__name__] = _context
sys.modules["app.cli.support"].context = _context
