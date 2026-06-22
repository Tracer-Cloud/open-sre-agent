"""Compatibility alias for interactive shell REPL progress helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.runtime import repl_progress as _repl_progress

sys.modules[__name__] = _repl_progress
sys.modules["app.cli.support"].repl_progress = _repl_progress
