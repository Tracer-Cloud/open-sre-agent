"""Unit tests for the shared-stack Terraform memories resolver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from platform.deployment_multi_tenant.utils.resolve_infra_aws_memories import (
    InfraAwsMemoriesResolutionError,
    main,
    parse_memories_environment,
    resolve_infra_aws_memories,
)

_VALID_ENTRY: dict[str, Any] = {
    "bucket_name": "opensre-memories-dev",
    "bucket_arn": "arn:aws:s3:::opensre-memories-dev",
    "file_system_id": "fs-abc123",
    "file_system_arn": "arn:aws:s3files:us-east-1:123456789012:file-system/fs-abc123",
    "client_security_group_id": "sg-deadbeef",
    "subnet_ids": ["subnet-aaa", "subnet-bbb"],
}


def test_parse_memories_environment_success() -> None:
    config = parse_memories_environment({"dev": _VALID_ENTRY}, environment="dev")
    assert config.environment == "dev"
    assert config.file_system_id == "fs-abc123"
    assert config.client_security_group_id == "sg-deadbeef"
    assert config.subnet_ids == ("subnet-aaa", "subnet-bbb")
    assert config.cdk_parameter_args() == (
        "--parameters",
        "S3FileSystemId=fs-abc123",
        "--parameters",
        "S3FileSystemArn=arn:aws:s3files:us-east-1:123456789012:file-system/fs-abc123",
        "--parameters",
        "S3FilesClientSecurityGroupId=sg-deadbeef",
    )


def test_parse_memories_environment_missing_environment() -> None:
    with pytest.raises(InfraAwsMemoriesResolutionError, match="absent"):
        parse_memories_environment({"prod": _VALID_ENTRY}, environment="dev")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("file_system_id", "not-a-fs", "file_system_id"),
        (
            "file_system_arn",
            "arn:aws:s3:::bucket",
            "file_system_arn",
        ),
        ("client_security_group_id", "not-an-sg", "client_security_group_id"),
        ("subnet_ids", [], "subnet_ids"),
        ("bucket_name", "", "bucket_name"),
    ],
)
def test_parse_memories_environment_rejects_invalid_fields(
    field: str,
    value: Any,
    match: str,
) -> None:
    entry = dict(_VALID_ENTRY)
    entry[field] = value
    with pytest.raises(InfraAwsMemoriesResolutionError, match=match):
        parse_memories_environment({"dev": entry}, environment="dev")


def test_resolve_infra_aws_memories_requires_checkout(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(InfraAwsMemoriesResolutionError, match="does not exist"):
        resolve_infra_aws_memories(infra_dir=missing, environment="dev")


def test_resolve_infra_aws_memories_requires_shared_stack(tmp_path: Path) -> None:
    with pytest.raises(InfraAwsMemoriesResolutionError, match="Missing shared stack"):
        resolve_infra_aws_memories(infra_dir=tmp_path, environment="dev")


def test_resolve_infra_aws_memories_requires_initialized_backend(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "stacks" / "shared"
    shared.mkdir(parents=True)
    with pytest.raises(InfraAwsMemoriesResolutionError, match="not initialized"):
        resolve_infra_aws_memories(infra_dir=tmp_path, environment="dev")


def test_resolve_infra_aws_memories_reads_terraform_output(tmp_path: Path) -> None:
    shared = tmp_path / "stacks" / "shared"
    shared.mkdir(parents=True)
    (shared / ".terraform").mkdir()

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"dev": _VALID_ENTRY})
    completed.stderr = ""

    with (
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.shutil.which",
            return_value="/usr/bin/terraform",
        ),
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.subprocess.run",
            return_value=completed,
        ) as run_mock,
    ):
        config = resolve_infra_aws_memories(infra_dir=tmp_path, environment="dev")

    assert config.file_system_id == "fs-abc123"
    run_mock.assert_called_once()
    command = run_mock.call_args.args[0]
    assert command[0] == "/usr/bin/terraform"
    assert f"-chdir={shared.resolve()}" in command
    assert command[-3:] == ("output", "-json", "memories")
    assert run_mock.call_args.kwargs["env"]["TF_WORKSPACE"] == "default"


def test_resolve_infra_aws_memories_unwraps_wrapped_output(tmp_path: Path) -> None:
    shared = tmp_path / "stacks" / "shared"
    shared.mkdir(parents=True)
    (shared / ".terraform").mkdir()

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps(
        {
            "sensitive": False,
            "type": ["object", {}],
            "value": {"dev": _VALID_ENTRY},
        }
    )
    completed.stderr = ""

    with (
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.shutil.which",
            return_value="/usr/bin/terraform",
        ),
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.subprocess.run",
            return_value=completed,
        ),
    ):
        config = resolve_infra_aws_memories(infra_dir=tmp_path, environment="dev")

    assert config.bucket_name == "opensre-memories-dev"


def test_resolve_infra_aws_memories_propagates_terraform_failure(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "stacks" / "shared"
    shared.mkdir(parents=True)
    (shared / ".terraform").mkdir()

    completed = MagicMock()
    completed.returncode = 1
    completed.stdout = ""
    completed.stderr = "Backend initialization required"

    with (
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.shutil.which",
            return_value="/usr/bin/terraform",
        ),
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.subprocess.run",
            return_value=completed,
        ),
        pytest.raises(InfraAwsMemoriesResolutionError, match="terraform output failed"),
    ):
        resolve_infra_aws_memories(infra_dir=tmp_path, environment="dev")


def test_main_prints_cdk_args(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shared = tmp_path / "stacks" / "shared"
    shared.mkdir(parents=True)
    (shared / ".terraform").mkdir()

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = json.dumps({"dev": _VALID_ENTRY})
    completed.stderr = ""

    with (
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.shutil.which",
            return_value="/usr/bin/terraform",
        ),
        patch(
            "platform.deployment_multi_tenant.utils.resolve_infra_aws_memories.subprocess.run",
            return_value=completed,
        ),
    ):
        exit_code = main(
            [
                "--infra-dir",
                str(tmp_path),
                "--environment",
                "dev",
                "--print-cdk-args",
            ]
        )

    assert exit_code == 0
    assert "S3FileSystemId=fs-abc123" in capsys.readouterr().out


def test_main_requires_infra_dir() -> None:
    with pytest.raises(SystemExit):
        main(["--environment", "dev"])
