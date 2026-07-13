"""Tests for the gateway AMI bake path."""

from __future__ import annotations

import pytest

import platform.deployment.gateway.stack as stack_module
from platform.deployment.aws.config import GATEWAY_AMI_GIT_REF_ENV
from platform.deployment.gateway import bake as bake_module

_FAKE_INSTANCE_ID = "i-builder1234567890"
_FAKE_AMI_ID = "ami-0abc1234567890def"


def _stub_bake_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every external side effect in bake_ami() except AMI id persistence."""
    monkeypatch.setattr(
        bake_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:aws:iam::1:instance-profile/p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(bake_module, "get_latest_al2023_ami", lambda *_a, **_kw: "ami-base")
    monkeypatch.setattr(
        bake_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": _FAKE_INSTANCE_ID}
    )
    monkeypatch.setattr(bake_module, "wait_for_running", lambda *_a, **_kw: None)
    monkeypatch.setattr(bake_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        bake_module,
        "run_ssm_shell_command",
        lambda *_a, **_kw: {"status": "Success", "stderr": ""},
    )
    monkeypatch.setattr(bake_module, "create_image_from_instance", lambda *_a, **_kw: _FAKE_AMI_ID)
    monkeypatch.setattr(bake_module, "terminate_instance", lambda *_a, **_kw: None)
    monkeypatch.setattr(bake_module, "delete_instance_profile", lambda *_a, **_kw: None)


def test_bake_ami_uses_env_var_git_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """bake_ami() uses OPENSRE_GATEWAY_GIT_REF when set."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.setenv(GATEWAY_AMI_GIT_REF_ENV, "v0.3.0")
    _stub_bake_dependencies(monkeypatch)

    captured_commands: list[list[str]] = []

    def capture_ssm(*_a: object, commands: list[str], **_kw: object) -> dict:
        captured_commands.append(commands)
        return {"status": "Success", "stderr": ""}

    monkeypatch.setattr(bake_module, "run_ssm_shell_command", capture_ssm)

    result = bake_module.bake_ami(ami_id_path=ami_id_file)

    assert result == _FAKE_AMI_ID
    joined = "\n".join(" ".join(c) if isinstance(c, list) else c for c in captured_commands[0])
    assert "v0.3.0" in joined


def test_bake_ami_saves_ami_id_to_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """bake_ami() persists the new AMI id so deploy can reuse it."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.delenv(GATEWAY_AMI_GIT_REF_ENV, raising=False)
    _stub_bake_dependencies(monkeypatch)
    # make _resolve_git_ref return a deterministic value without running git
    monkeypatch.setattr(bake_module, "_resolve_git_ref", lambda: "deadbeef1234")

    bake_module.bake_ami(ami_id_path=ami_id_file)

    assert ami_id_file.exists()
    assert ami_id_file.read_text().strip() == _FAKE_AMI_ID


def test_bake_ami_terminates_builder_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Builder instance is terminated even when the install script fails."""
    ami_id_file = tmp_path / "gateway-id.txt"
    monkeypatch.setattr(stack_module, "_AMI_ID_FILE", ami_id_file)
    monkeypatch.setenv(GATEWAY_AMI_GIT_REF_ENV, "main")

    terminated: list[str] = []

    monkeypatch.setattr(
        bake_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(bake_module, "get_latest_al2023_ami", lambda *_a, **_kw: "ami-base")
    monkeypatch.setattr(
        bake_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": _FAKE_INSTANCE_ID}
    )
    monkeypatch.setattr(bake_module, "wait_for_running", lambda *_a, **_kw: None)
    monkeypatch.setattr(bake_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        bake_module,
        "run_ssm_shell_command",
        lambda *_a, **_kw: {"status": "Failed", "stderr": "something went wrong"},
    )
    monkeypatch.setattr(
        bake_module, "terminate_instance", lambda iid, *_a, **_kw: terminated.append(iid)
    )
    monkeypatch.setattr(bake_module, "delete_instance_profile", lambda *_a, **_kw: None)

    with pytest.raises(RuntimeError, match="Install commands failed"):
        bake_module.bake_ami(ami_id_path=ami_id_file)

    assert _FAKE_INSTANCE_ID in terminated
