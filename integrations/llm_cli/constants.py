"""Shared constants for subprocess-backed LLM CLI adapters.

Keep this module focused on values reused by multiple adapters/runner paths.
Provider-specific constants should remain in each adapter module.
"""

from __future__ import annotations

from typing import Final

# Runner cache/retry knobs.
PROBE_CACHE_TTL_SEC: Final[float] = 45.0
EX_TEMPFAIL: Final[int] = 75
TEMPFAIL_MAX_RETRIES: Final[int] = 2
TEMPFAIL_BACKOFF_SEC: Final[float] = 2.0

# Shared default for CLI subprocess invokes (investigation ReAct sends large prompts).
DEFAULT_EXEC_TIMEOUT_SEC: Final[float] = 300.0
MIN_EXEC_TIMEOUT_SEC: Final[float] = 30.0
MAX_EXEC_TIMEOUT_SEC: Final[float] = 600.0
