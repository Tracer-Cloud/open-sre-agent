"""Regression tests for E2E deploy helper path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.upstream_apache_flink_ecs.infrastructure_sdk import deploy as flink_deploy
from tests.e2e.upstream_lambda.infrastructure_sdk import deploy as lambda_deploy
from tests.e2e.upstream_prefect_ecs_fargate.infrastructure_sdk import deploy as prefect_deploy

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "module",
    [lambda_deploy, prefect_deploy, flink_deploy],
)
def test_e2e_deploy_helpers_resolve_repo_root(module: object) -> None:
    assert module.project_root == REPO_ROOT


@pytest.mark.parametrize(
    ("module", "paths"),
    [
        (
            lambda_deploy,
            {
                "SHARED_VENDOR_API_DIR": REPO_ROOT / "tests" / "shared" / "external_vendor_api",
                "PIPELINE_CODE_DIR": REPO_ROOT
                / "tests"
                / "e2e"
                / "upstream_lambda"
                / "pipeline_code",
            },
        ),
        (
            prefect_deploy,
            {
                "MOCK_API_CODE": REPO_ROOT / "tests" / "shared" / "external_vendor_api",
                "TRIGGER_LAMBDA_CODE": REPO_ROOT
                / "tests"
                / "e2e"
                / "upstream_prefect_ecs_fargate"
                / "pipeline_code"
                / "trigger_lambda",
            },
        ),
        (
            flink_deploy,
            {
                "FLINK_IMAGE_DOCKERFILE": REPO_ROOT
                / "tests"
                / "e2e"
                / "upstream_apache_flink_ecs"
                / "infrastructure_code"
                / "flink_image"
                / "Dockerfile",
                "MOCK_API_CODE_DIR": REPO_ROOT / "tests" / "shared" / "external_vendor_api",
                "TRIGGER_LAMBDA_CODE_DIR": REPO_ROOT
                / "tests"
                / "e2e"
                / "upstream_apache_flink_ecs"
                / "pipeline_code"
                / "trigger_lambda",
            },
        ),
    ],
)
def test_e2e_deploy_helper_asset_paths_resolve_existing_repo_assets(
    module: object,
    paths: dict[str, Path],
) -> None:
    for attr, expected in paths.items():
        actual = getattr(module, attr)
        assert actual == expected
        assert actual.exists(), f"{attr} does not exist: {actual}"
