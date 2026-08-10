"""Approval prompts must never render raw secrets into multi-member chats."""

from __future__ import annotations

from gateway.core.runtime.approvals import _REDACTED, arguments_preview


def test_arguments_preview_redacts_sensitive_keys() -> None:
    preview = arguments_preview(
        {
            "channel": "ops",
            "api_key": "sk-live-super-secret",
            "token": "xoxb-should-not-leak",
            "password": "hunter2",
            "message": "restart checkout",
        }
    )

    assert "sk-live-super-secret" not in preview
    assert "xoxb-should-not-leak" not in preview
    assert "hunter2" not in preview
    assert "restart checkout" in preview
    # The key is still named so the reviewer can see a credential was passed;
    # only the value is replaced.
    assert f"api_key: {_REDACTED}" in preview


def test_arguments_preview_scrubs_bearer_tokens_under_neutral_keys() -> None:
    preview = arguments_preview(
        {"headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz012345"}}
    )

    assert "abcdefghijklmnopqrstuvwxyz012345" not in preview
    # A nested mapping is summarised by its key names, never its values, so a
    # secret under a neutral key cannot reach the channel in the first place.
    assert "headers: 1 fields (Authorization)" in preview
