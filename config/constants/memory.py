"""Environment names and static limits for long-term memory."""

from __future__ import annotations

OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV = "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED"
OPENSRE_MEMORY_DIR_ENV = "OPENSRE_MEMORY_DIR"
OPENSRE_MEMORY_DISABLED_ENV = "OPENSRE_MEMORY_DISABLED"

# Bound how long session close waits for background extraction after resources
# are released. Long enough for a normal classification call; short enough that
# a hung provider cannot stall shell exit indefinitely.
MEMORY_EXTRACTION_JOIN_TIMEOUT_SECONDS = 15.0

__all__ = [
    "MEMORY_EXTRACTION_JOIN_TIMEOUT_SECONDS",
    "OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV",
    "OPENSRE_MEMORY_DIR_ENV",
    "OPENSRE_MEMORY_DISABLED_ENV",
]
