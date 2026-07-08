"""Tests for the gateway AMI lifecycle (deploy and destroy)."""

from __future__ import annotations

import pytest

import platform.deployment.gateway.stack as stack_module
from platform.deployment.aws.config import GATEWAY_AMI_DESTROY_PURGE_ENV, GATEWAY_AMI_ID_ENV
from platform.deployment.gateway import lifecycle as lifecycle_module

_FAKE_AMI_ID = "ami-0abc1234567890def"
_FAKE_INSTANCE_ID = "i-gateway1234567890"


def _stub_deploy_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lifecycle_module, "validate_deploy_env", lambda: None)
    monkeypatch.setattr(lifecycle_module, "cleanup_existing_deployment", lambda **_kw: False)
    monkeypatch.setattr(
        lifecycle_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:aws:iam::1:instance-profile/p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(
        lifecycle_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": _FAKE_INSTANCE_ID}
    )
    monkeypatch.setattr(
        lifecycle_module,
        "wait_for_running",
        lambda iid, *_a, **_kw: {"InstanceId": iid, "PublicIpAddress": "1.2.3.4"},
    )
    monkeypatch.setattr(lifecycle_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle_module, "provision_gateway_via_ssm", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle_module, "wait_for_gateway_ready", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle_module, "save_outputs", lambda *_a, **_kw: None)


def test_deploy_returns_required_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """deploy() must return InstanceId, PublicIpAddress, and AmiId."""
    monkeypatch.setenv(GATEWAY_AMI_ID_ENV, _FAKE_AMI_ID)
    _stub_deploy_dependencies(monkeypatch)

    outputs = lifecycle_module.deploy()

    assert outputs["InstanceId"] == _FAKE_INSTANCE_ID
    assert outputs["PublicIpAddress"] == "1.2.3.4"
    assert outputs["AmiId"] == _FAKE_AMI_ID


def test_deploy_uses_saved_ami_id_when_env_not_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """deploy() falls back to the saved AMI id file when OPENSRE_GATEWAY_AMI_ID is not set."""
    ami_id_file = tmp_path / "gateway-id.txt"
    ami_id_file.write_text(_FAKE_AMI_ID + "\n")
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.delenv(GATEWAY_AMI_ID_ENV, raising=False)
    _stub_deploy_dependencies(monkeypatch)

    outputs = lifecycle_module.deploy()

    assert outputs["AmiId"] == _FAKE_AMI_ID


def test_deploy_fails_fast_when_no_ami_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """deploy() raises RuntimeError when no AMI id is available."""
    monkeypatch.delenv(GATEWAY_AMI_ID_ENV, raising=False)
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", tmp_path / "nonexistent.txt")
    monkeypatch.setattr(lifecycle_module, "validate_deploy_env", lambda: None)

    with pytest.raises(RuntimeError, match="bake-gateway"):
        lifecycle_module.deploy()


def _stub_destroy_dependencies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub every destroy() side effect; return list of deregister_image calls."""
    deregister_calls: list[str] = []

    monkeypatch.setattr(
        lifecycle_module,
        "load_outputs",
        lambda: {"InstanceId": _FAKE_INSTANCE_ID, "AmiId": _FAKE_AMI_ID},
    )
    monkeypatch.setattr(lifecycle_module, "terminate_instance", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle_module, "delete_instance_profile", lambda *_a, **_kw: None)
    monkeypatch.setattr(lifecycle_module, "delete_outputs", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        lifecycle_module,
        "deregister_image",
        lambda ami_id, *_a, **_kw: deregister_calls.append(ami_id),
    )
    return deregister_calls


def test_destroy_keeps_ami_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """destroy() must not deregister the AMI unless opted in."""
    monkeypatch.delenv(GATEWAY_AMI_DESTROY_PURGE_ENV, raising=False)
    deregister_calls = _stub_destroy_dependencies(monkeypatch)

    results = lifecycle_module.destroy()

    assert deregister_calls == []
    assert not any(item.startswith("ami:") for item in results["deleted"])


def test_cleanup_terminates_orphans_then_runs_destroy_for_iam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orphan instances without an outputs file still trigger destroy() for IAM cleanup."""
    terminated: list[str] = []
    destroy_calls: list[int] = []

    monkeypatch.delenv("OPENSRE_DEPLOY_ABORT_IF_EXISTS", raising=False)
    monkeypatch.setattr(lifecycle_module, "outputs_exists", lambda: False)
    monkeypatch.setattr(
        lifecycle_module,
        "find_stack_instance_ids",
        lambda *_args, **_kwargs: ["i-orphan"],
    )
    monkeypatch.setattr(
        lifecycle_module,
        "terminate_instance",
        lambda instance_id, _region: terminated.append(instance_id),
    )
    monkeypatch.setattr(lifecycle_module, "destroy", lambda: destroy_calls.append(1) or {})

    assert lifecycle_module.cleanup_existing_deployment() is True
    assert terminated == ["i-orphan"]
    assert destroy_calls == [1]


def test_destroy_purges_ami_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """destroy() deregisters the AMI when OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1."""
    monkeypatch.setenv(GATEWAY_AMI_DESTROY_PURGE_ENV, "1")
    deregister_calls = _stub_destroy_dependencies(monkeypatch)

    results = lifecycle_module.destroy()

    assert _FAKE_AMI_ID in deregister_calls
    assert any(item.startswith("ami:") for item in results["deleted"])
