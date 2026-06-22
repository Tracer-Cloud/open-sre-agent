"""Compatibility alias for interactive shell error helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.error_handling import errors as _errors

sys.modules[__name__] = _errors
sys.modules["app.cli.support"].errors = _errors
