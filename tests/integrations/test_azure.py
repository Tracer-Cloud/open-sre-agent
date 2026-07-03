from __future__ import annotations

from integrations.azure.verifier import verify_azure


def test_verify_azure_missing_workspace_id() -> None:
    result = verify_azure("local env", {"access_token": "token"})

    assert result["status"] == "missing"
    assert "workspace_id" in result["detail"]


def test_verify_azure_missing_access_token() -> None:
    result = verify_azure("local env", {"workspace_id": "workspace"})

    assert result["status"] == "missing"
    assert "access_token" in result["detail"]


def test_verify_azure_passes_with_default_endpoint() -> None:
    result = verify_azure(
        "local env",
        {"workspace_id": "workspace", "access_token": "token"},
    )

    assert result["status"] == "passed"
    assert "https://api.loganalytics.io" in result["detail"]


def test_verify_azure_passes_with_custom_endpoint() -> None:
    result = verify_azure(
        "local env",
        {
            "workspace_id": "workspace",
            "access_token": "token",
            "endpoint": "https://custom.loganalytics.example.com",
        },
    )

    assert result["status"] == "passed"
    assert "https://custom.loganalytics.example.com" in result["detail"]
