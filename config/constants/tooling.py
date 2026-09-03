"""Shared constants for the tool contracts and registry."""

from __future__ import annotations

from typing import Final

# Approval tokens auto-expire after this many seconds (5 minutes).
DEFAULT_APPROVAL_EXPIRY_SECONDS: Final[int] = 300

__all__ = ["DEFAULT_APPROVAL_EXPIRY_SECONDS"]
