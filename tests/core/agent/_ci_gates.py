"""CI vs local behavior for turn tests that may skip when prerequisites are missing."""

from __future__ import annotations

import os

import pytest


def running_in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"


def skip_or_fail(message: str) -> None:
    """Fail in CI (required gate); skip locally (optional prerequisites)."""
    if running_in_github_actions():
        pytest.fail(message)
    pytest.skip(message)
