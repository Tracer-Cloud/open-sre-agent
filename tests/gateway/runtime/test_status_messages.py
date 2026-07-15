"""Tests for gateway user-facing error copy (CWE-209 redaction)."""

from __future__ import annotations

from core.llm.shared.llm_retry import CREDIT_EXHAUSTED_MARKER
from gateway.runtime.status_messages import user_facing_error_message


def test_generic_error_hides_raw_exception_detail() -> None:
    # Arrange: an internal error string carrying host/credential detail that
    # must never reach a chat user.
    detail = "ValueError: connection to db-prod-primary:5432 failed for user svc_acct"

    # Act
    shown = user_facing_error_message(detail)

    # Assert: the user sees only generic copy; no internal detail survives.
    assert shown == "Something went wrong handling that request. Please try again."
    assert "db-prod-primary" not in shown
    assert "svc_acct" not in shown


def test_credit_exhausted_gives_actionable_guidance() -> None:
    # Arrange: an error flagged with the credit-exhausted marker.
    detail = f"anthropic {CREDIT_EXHAUSTED_MARKER}. account balance too low"

    # Act
    shown = user_facing_error_message(detail)

    # Assert: user gets re-auth guidance, not the raw provider billing text.
    assert "opensre auth login" in shown
    assert "balance too low" not in shown
