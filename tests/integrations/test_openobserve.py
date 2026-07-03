from __future__ import annotations

from integrations.openobserve.verifier import verify_openobserve


def test_verify_openobserve_missing_base_url() -> None:
    result = verify_openobserve("local env", {"api_token": "token"})

    assert result["status"] == "missing"
    assert "base_url" in result["detail"]


def test_verify_openobserve_missing_auth() -> None:
    result = verify_openobserve("local env", {"base_url": "https://openobserve.example.com"})

    assert result["status"] == "missing"
    assert "api token" in result["detail"].lower()


def test_verify_openobserve_passes_with_api_token() -> None:
    result = verify_openobserve(
        "local env",
        {"base_url": "https://openobserve.example.com/", "api_token": "token"},
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("at https://openobserve.example.com.")


def test_verify_openobserve_passes_with_username_password() -> None:
    result = verify_openobserve(
        "local env",
        {
            "base_url": "https://openobserve.example.com",
            "username": "user",
            "password": "pass",
        },
    )

    assert result["status"] == "passed"
    assert result["detail"].endswith("at https://openobserve.example.com.")
