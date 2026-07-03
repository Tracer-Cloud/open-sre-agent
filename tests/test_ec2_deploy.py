from __future__ import annotations

import pytest

from platform.deployment.ecr_deploy import lifecycle as deploy_module

_FAKE_IMAGE_URI = "123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre:latest"


def test_deploy_returns_all_required_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deploy() must return InstanceId, PublicIpAddress, and infrastructure keys.

    With OPENSRE_IMAGE_URI set, deploy() skips the ECR build entirely and uses
    the supplied URI directly.
    """

    def fake_create_instance_profile(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {
            "ProfileName": "profile-123",
            "ProfileArn": "arn:aws:iam::123:instance-profile/profile-123",
            "RoleName": "role-123",
        }

    def fake_get_latest_ami(*_args: object, **_kwargs: object) -> str:
        return "ami-123"

    def fake_launch_instance(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"InstanceId": "i-123"}

    def fake_wait_for_running(
        instance_id: str, *_args: object, **_kwargs: object
    ) -> dict[str, str]:
        return {"InstanceId": instance_id, "PublicIpAddress": "54.1.2.3"}

    monkeypatch.setenv("OPENSRE_IMAGE_URI", _FAKE_IMAGE_URI)
    monkeypatch.setattr(deploy_module, "validate_deploy_env", lambda: None)
    monkeypatch.setattr(deploy_module, "cleanup_existing_deployment", lambda **_kw: False)
    monkeypatch.setattr(deploy_module, "create_instance_profile", fake_create_instance_profile)
    monkeypatch.setattr(deploy_module, "get_latest_al2023_ami", fake_get_latest_ami)
    monkeypatch.setattr(deploy_module, "launch_instance", fake_launch_instance)
    monkeypatch.setattr(deploy_module, "wait_for_running", fake_wait_for_running)
    monkeypatch.setattr(deploy_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy_module, "provision_instance_via_ssm", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy_module, "wait_for_deployment_ready", lambda **_kw: None)
    monkeypatch.setattr(deploy_module, "save_outputs", lambda *_a, **_kw: None)

    outputs = deploy_module.deploy()

    assert outputs["PublicIpAddress"] == "54.1.2.3"
    assert outputs["InstanceId"] == "i-123"
    assert outputs["ImageUri"] == _FAKE_IMAGE_URI
    assert "SecurityGroupId" not in outputs
    assert "VpcId" not in outputs
    assert "SubnetId" not in outputs


def test_deploy_uses_saved_image_uri_when_env_not_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """deploy() falls back to the saved URI file when OPENSRE_IMAGE_URI is not set."""
    import platform.deployment.ecr_deploy.stack as stack_module

    uri_file = tmp_path / "image-uri.txt"
    uri_file.write_text(_FAKE_IMAGE_URI + "\n")

    monkeypatch.delenv("OPENSRE_IMAGE_URI", raising=False)
    monkeypatch.setattr(stack_module, "_IMAGE_URI_FILE", uri_file)

    monkeypatch.setattr(deploy_module, "validate_deploy_env", lambda: None)
    monkeypatch.setattr(deploy_module, "cleanup_existing_deployment", lambda **_kw: False)
    monkeypatch.setattr(
        deploy_module,
        "create_instance_profile",
        lambda *_a, **_kw: {
            "ProfileName": "p",
            "ProfileArn": "arn:aws:iam::1:instance-profile/p",
            "RoleName": "r",
        },
    )
    monkeypatch.setattr(deploy_module, "get_latest_al2023_ami", lambda *_a, **_kw: "ami-x")
    monkeypatch.setattr(deploy_module, "launch_instance", lambda *_a, **_kw: {"InstanceId": "i-x"})
    monkeypatch.setattr(
        deploy_module,
        "wait_for_running",
        lambda instance_id, *_a, **_kw: {"InstanceId": instance_id, "PublicIpAddress": "1.2.3.4"},
    )
    monkeypatch.setattr(deploy_module, "wait_for_ssm_registration", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy_module, "provision_instance_via_ssm", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy_module, "wait_for_deployment_ready", lambda **_kw: None)
    monkeypatch.setattr(deploy_module, "save_outputs", lambda *_a, **_kw: None)

    outputs = deploy_module.deploy()
    assert outputs["ImageUri"] == _FAKE_IMAGE_URI
