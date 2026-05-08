"""Tests for Helm CLI client behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.config_models import HelmIntegrationConfig
from app.services.helm.client import HelmClient


def _client() -> HelmClient:
    return HelmClient(
        HelmIntegrationConfig(
            helm_path="helm",
            kube_context="",
            kubeconfig="",
            default_namespace="",
            integration_id="test",
        )
    )


def test_helm_probe_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: None)
    client = _client()
    result = client.probe_access()
    assert result.status == "missing"
    assert "not found" in result.detail.lower()


def test_helm_probe_passes_when_version_and_list_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"version":"v3"}', stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is True
    assert "Helm CLI" in result.detail


def test_helm_probe_fails_when_list_stdout_is_not_valid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"version":"v3"}', stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="WARNING: banner\nnot-json", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is False
    assert "json" in result.detail.lower()


def test_helm_probe_fails_when_list_stdout_is_not_a_json_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"version":"v3"}', stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"releases":[]}', stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is False
    assert "array" in result.detail.lower()


def test_helm_probe_fails_when_list_stdout_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"version":"v3"}', stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is False
    assert "empty" in result.detail.lower()


def test_helm_list_parses_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")

    payload = '[{"name": "demo", "namespace": "demo"}]'

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    out = _client().list_releases(all_namespaces=True, max_releases=10)
    assert out["success"] is True
    assert out["releases"][0]["name"] == "demo"


def test_helm_status_requires_release_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")
    out = _client().release_status("", "demo")
    assert out["success"] is False
    assert "required" in out["error"].lower()
