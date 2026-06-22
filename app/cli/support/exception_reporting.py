"""Compatibility alias for interactive shell exception reporting helpers."""

from __future__ import annotations

import sys

from app.cli.interactive_shell.error_handling import exception_reporting as _exception_reporting

sys.modules[__name__] = _exception_reporting
sys.modules["app.cli.support"].exception_reporting = _exception_reporting
