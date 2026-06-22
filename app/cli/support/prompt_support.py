"""Compatibility alias for interactive shell prompt support helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.ui import prompt_support as _prompt_support

sys.modules[__name__] = _prompt_support
sys.modules["app.cli.support"].prompt_support = _prompt_support
