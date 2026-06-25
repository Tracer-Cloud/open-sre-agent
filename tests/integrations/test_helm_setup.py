"""Interactive setup coverage for the Helm integration."""

from __future__ import annotations

from unittest.mock import patch

from app.integrations.cli import _setup_helm, cmd_setup
from app.integrations.registry import SUPPORTED_SETUP_SERVICES


def test_setup_helm_persists_credentials() -> None:
    prompts = {
        "Helm binary path or name": "/usr/local/bin/helm",
        "Kubernetes context (optional, passed as --kube-context)": "prod-admin",
        "Kubeconfig file path (optional, passed as --kubeconfig)": "~/.kube/config",
        "Default namespace when alerts do not specify one (optional)": "production",
    }

    def fake_prompt(label: str, default: str = "", secret: bool = False) -> str:
        del secret
        return prompts.get(label, default)

    with (
        patch("app.integrations.cli._p", side_effect=fake_prompt),
        patch("app.integrations.cli.upsert_integration") as mock_upsert,
    ):
        _setup_helm()

    mock_upsert.assert_called_once_with(
        "helm",
        {
            "credentials": {
                "helm_path": "/usr/local/bin/helm",
                "kube_context": "prod-admin",
                "kubeconfig": "~/.kube/config",
                "default_namespace": "production",
            }
        },
    )


def test_cmd_setup_helm_dispatches_handler() -> None:
    calls: list[str] = []

    def fake_handler() -> None:
        calls.append("helm")

    with patch.dict("app.integrations.cli._HANDLERS", {"helm": fake_handler}):
        resolved = cmd_setup("helm")

    assert resolved == "helm"
    assert calls == ["helm"]


def test_helm_is_registered_for_setup() -> None:
    from app.integrations.cli import _HANDLERS

    assert "helm" in SUPPORTED_SETUP_SERVICES
    assert "helm" in _HANDLERS
