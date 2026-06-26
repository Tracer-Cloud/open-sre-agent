"""CI vs local behavior for routing tests that may skip when prerequisites are missing."""

from __future__ import annotations

import os

import pytest


def running_in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"


INVESTIGATION_LOOP_DISABLED_SKIP_MESSAGE = (
    "Natural-language investigation loop is disabled in the interactive shell "
    "(feature_flags.INTERACTIVE_SHELL_INVESTIGATION_ENABLED is False); "
    "this investigation scenario does not apply. Re-enable the flag to run it."
)


def skip_or_fail(message: str) -> None:
    """Fail in CI (required gate); skip locally (optional prerequisites)."""
    if running_in_github_actions():
        pytest.fail(message)
    pytest.skip(message)


def skip_investigation_loop_disabled() -> None:
    """Skip investigation dispatch scenarios when the NL loop kill-switch is off."""
    pytest.skip(INVESTIGATION_LOOP_DISABLED_SKIP_MESSAGE)


def is_allowed_live_llm_skip_in_ci(skip_repr: object) -> bool:
    """Return True for live_llm skips that are expected even in CI."""
    return INVESTIGATION_LOOP_DISABLED_SKIP_MESSAGE in str(skip_repr)
