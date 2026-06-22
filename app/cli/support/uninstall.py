"""Compatibility alias for interactive shell data-store uninstall helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.data_store import uninstall as _uninstall

sys.modules[__name__] = _uninstall
sys.modules["app.cli.support"].uninstall = _uninstall
