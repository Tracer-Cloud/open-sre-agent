"""Compatibility alias for interactive shell data-store constants."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.data_store import constants as _constants

sys.modules[__name__] = _constants
sys.modules["app.cli.support"].constants = _constants
