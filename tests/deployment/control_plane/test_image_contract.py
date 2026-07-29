from __future__ import annotations

from pathlib import Path

import pytest

from platform.deployment_multi_tenant.lambda_control_plane.utils.images import (
    CONTROL_PLANE_LAMBDA,
    GATEWAY_IMAGE,
    require_immutable_ecr_image,
)

REPOSITORY_ROOT = Path(__file__).parents[3]


def test_gateway_image_contains_required_runtime_tools_and_non_root_user() -> None:
    dockerfile = (REPOSITORY_ROOT / GATEWAY_IMAGE.dockerfile).read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    for package in ("bash", "ca-certificates", "curl"):
        assert package in dockerfile
    assert 'CMD ["sh"' in dockerfile
    assert "USER opensre" in dockerfile
    assert "--uid 1000 --gid 1000" in dockerfile
    assert GATEWAY_IMAGE.buildx_flags() == (
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--sbom=false",
        "--push",
    )


def test_lambda_uses_aws_managed_python_zip_runtime() -> None:
    assert CONTROL_PLANE_LAMBDA.runtime == "python3.12"
    assert CONTROL_PLANE_LAMBDA.architecture == "x86_64"
    assert CONTROL_PLANE_LAMBDA.package_type == "Zip"
    assert (
        CONTROL_PLANE_LAMBDA.handler
        == "platform.deployment_multi_tenant.lambda_control_plane.runtime.lambda_handler"
    )


def test_immutable_ecr_image_requires_digest() -> None:
    image = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/opensre@sha256:" + ("a" * 64)

    assert require_immutable_ecr_image(image) == image


@pytest.mark.parametrize(
    "image",
    [
        "opensre:latest",
        "123456789012.dkr.ecr.eu-west-1.amazonaws.com/opensre:latest",
        "public.ecr.aws/example/opensre@sha256:" + ("a" * 64),
        "123456789012.dkr.ecr.eu-west-1.amazonaws.com/opensre@sha256:short",
    ],
)
def test_immutable_ecr_image_rejects_mutable_or_non_ecr_references(image: str) -> None:
    with pytest.raises(ValueError, match="pinned"):
        require_immutable_ecr_image(image)
