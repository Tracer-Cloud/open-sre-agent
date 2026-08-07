from __future__ import annotations

from gateway.core.attachments.inline import scrub_secrets


def test_scrub_secrets_redacts_bearer_tokens() -> None:
    raw = "Authorization: Bearer abcdefghijklmnopqrst"
    assert "[REDACTED]" in scrub_secrets(raw)
