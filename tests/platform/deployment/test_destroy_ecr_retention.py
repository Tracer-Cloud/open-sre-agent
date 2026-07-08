"""Tests for ECR retention behavior in destroy()."""

from __future__ import annotations

import pytest

from platform.deployment.ecr_deploy import lifecycle


def _stub_destroy_dependencies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub every destroy() side effect except ECR deletion; return delete_repository calls."""
    ecr_delete_calls: list[str] = []

    monkeypatch.setattr(lifecycle, "load_outputs", lambda: {"InstanceId": "i-123"})
    monkeypatch.setattr(lifecycle, "terminate_instance", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle, "delete_instance_profile", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle, "delete_outputs", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        lifecycle.ecr,
        "delete_repository",
        lambda name, *_a, **_kw: ecr_delete_calls.append(name),
    )
    return ecr_delete_calls


def test_destroy_keeps_ecr_repository_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """destroy() must not delete the ECR repository unless explicitly opted in."""
    monkeypatch.delenv("OPENSRE_DESTROY_PURGE_ECR", raising=False)
    ecr_delete_calls = _stub_destroy_dependencies(monkeypatch)

    results = lifecycle.destroy()

    assert ecr_delete_calls == []
    assert not any(item.startswith("ecr-repository:") for item in results["deleted"])


def test_destroy_purges_ecr_repository_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """destroy() deletes the ECR repository when OPENSRE_DESTROY_PURGE_ECR is set."""
    monkeypatch.setenv("OPENSRE_DESTROY_PURGE_ECR", "1")
    ecr_delete_calls = _stub_destroy_dependencies(monkeypatch)

    results = lifecycle.destroy()

    assert ecr_delete_calls == [lifecycle.get_stack().ecr_repo_name]
    assert any(item.startswith("ecr-repository:") for item in results["deleted"])
