"""Compatibility alias for interactive shell data-store argument helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.data_store import args as _args

sys.modules[__name__] = _args
sys.modules["app.cli.support"].args = _args
