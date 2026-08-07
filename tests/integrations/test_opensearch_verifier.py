"""verify_opensearch enforces auth-method coherence, not just URL presence.

These rules used to live in a setup-only ``validate`` hook; moving them into the
verifier means ``integrations verify opensearch`` (health checks) rejects the
same bad combinations setup does — one definition of "configured".
"""

from __future__ import annotations

from integrations.opensearch.verifier import verify_opensearch


def test_url_only_passes() -> None:
    outcome = verify_opensearch("setup", {"url": "https://os.example.com"})
    assert outcome["status"] == "passed"


def test_missing_url_is_reported_missing() -> None:
    outcome = verify_opensearch("setup", {"username": "admin", "password": "pw"})
    assert outcome["status"] == "missing"


def test_complete_basic_auth_passes() -> None:
    outcome = verify_opensearch(
        "setup", {"url": "https://os.example.com", "username": "admin", "password": "pw"}
    )
    assert outcome["status"] == "passed"


def test_api_key_only_passes() -> None:
    outcome = verify_opensearch("setup", {"url": "https://os.example.com", "api_key": "k"})
    assert outcome["status"] == "passed"


def test_half_basic_auth_fails() -> None:
    """Username without a password (or the reverse) omits Authorization at runtime."""
    outcome = verify_opensearch("setup", {"url": "https://os.example.com", "username": "admin"})
    assert outcome["status"] == "failed"


def test_api_key_and_basic_auth_together_fails() -> None:
    """Runtime prioritizes the API key, so a stale key would shadow basic creds."""
    outcome = verify_opensearch(
        "setup",
        {
            "url": "https://os.example.com",
            "api_key": "k",
            "username": "admin",
            "password": "pw",
        },
    )
    assert outcome["status"] == "failed"
