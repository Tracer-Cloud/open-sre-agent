"""Compatibility alias for interactive shell CLI error mapping."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.error_handling import cli_error_mapping as _cli_error_mapping

sys.modules[__name__] = _cli_error_mapping
sys.modules["app.cli.support"].cli_error_mapping = _cli_error_mapping
