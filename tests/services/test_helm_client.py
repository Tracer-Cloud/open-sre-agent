"""Tests for Helm CLI client behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.config_models import HelmIntegrationConfig
from app.services.helm.client import HelmClient, _helm_client_major_version

_HELM_V3_VERSION_STDOUT = (
    'Client: version.BuildInfo{Version:"v3.14.0", GitCommit:"abc", GoVersion:"go1.22"}\n'
)


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


def test_helm_client_major_version_parses_helm2_and_helm3_output() -> None:
    assert (
        _helm_client_major_version('Client: &version.Version{SemVer:"v2.17.0", GitCommit:""}') == 2
    )
    assert _helm_client_major_version('version.BuildInfo{Version:"v3.0.0", GitCommit:""}') == 3


def test_helm_probe_passes_when_version_and_list_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout=_HELM_V3_VERSION_STDOUT, stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is True
    assert "Helm CLI" in result.detail


def test_helm_probe_rejects_helm2_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")
    helm2_out = 'Client: &version.Version{SemVer:"v2.17.0", GitCommit:""}\n'

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout=helm2_out, stderr="")
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    result = _client().probe_access()
    assert result.ok is False
    assert "helm 3" in result.detail.lower()


def test_helm_probe_fails_when_list_stdout_is_not_valid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/usr/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        if "version" in cmd:
            return SimpleNamespace(returncode=0, stdout=_HELM_V3_VERSION_STDOUT, stderr="")
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
            return SimpleNamespace(returncode=0, stdout=_HELM_V3_VERSION_STDOUT, stderr="")
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
            return SimpleNamespace(returncode=0, stdout=_HELM_V3_VERSION_STDOUT, stderr="")
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


def test_helm_list_reports_all_namespaces_when_cli_uses_dash_a_with_empty_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default branch passes `-A`; response metadata must not claim single-namespace mode."""
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        cmds.append(list(cmd))
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    out = _client().list_releases(all_namespaces=False, namespace="", max_releases=10)
    assert out["success"] is True
    assert out["all_namespaces"] is True
    assert any(part == "-A" for part in cmds[0])


def test_helm_list_reports_single_namespace_when_cli_uses_dash_n(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        cmds.append(list(cmd))
        if "list" in cmd:
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    out = _client().list_releases(all_namespaces=False, namespace="prod", max_releases=10)
    assert out["success"] is True
    assert out["all_namespaces"] is False
    assert out["namespace"] == "prod"
    assert "-n" in cmds[0] and "prod" in cmds[0]


def test_helm_status_requires_release_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")
    out = _client().release_status("", "demo")
    assert out["success"] is False
    assert "required" in out["error"].lower()


def test_helm_get_values_treats_json_null_as_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helm 3 prints the JSON literal null for releases installed without custom values."""
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        for i, part in enumerate(cmd):
            if part == "get" and i + 1 < len(cmd) and cmd[i + 1] == "values":
                return SimpleNamespace(returncode=0, stdout="null\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    out = _client().get_values("my-release", "default")
    assert out["success"] is True
    assert out["values"] == {}
    assert out["release"] == "my-release"
    assert out["namespace"] == "default"


def test_helm_get_manifest_truncates_using_helm_manifest_max_chars_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HELM_MANIFEST_MAX_CHARS", "5000")
    monkeypatch.setattr("app.services.helm.client.shutil.which", lambda _name: "/bin/helm")
    payload = "x" * 8000

    def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
        for i, part in enumerate(cmd):
            if part == "get" and i + 1 < len(cmd) and cmd[i + 1] == "manifest":
                return SimpleNamespace(returncode=0, stdout=payload, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    monkeypatch.setattr("app.services.helm.client.subprocess.run", fake_run)
    out = _client().get_manifest("rel", "ns")
    assert out["success"] is True
    assert len(out["manifest"]) == 5000
    assert out["truncated"] is True
