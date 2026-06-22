"""Compatibility alias for interactive shell data-store update helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.data_store import update as _update

sys.modules[__name__] = _update
sys.modules["app.cli.support"].update = _update
