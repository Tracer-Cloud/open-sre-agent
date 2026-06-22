"""Compatibility alias for interactive shell exit-code constants."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.error_handling import exit_codes as _exit_codes

sys.modules[__name__] = _exit_codes
sys.modules["app.cli.support"].exit_codes = _exit_codes
