"""Regression coverage for E2E deployment helper path resolution."""

from pathlib import Path

import pytest

from tests.e2e._paths import find_repo_root


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS = [
    REPO_ROOT
    / "tests"
    / "e2e"
    / "upstream_lambda"
    / "infrastructure_sdk"
    / "deploy.py",
    REPO_ROOT
    / "tests"
    / "e2e"
    / "upstream_prefect_ecs_fargate"
    / "infrastructure_sdk"
    / "deploy.py",
    REPO_ROOT
    / "tests"
    / "e2e"
    / "upstream_apache_flink_ecs"
    / "infrastructure_sdk"
    / "deploy.py",
]


@pytest.mark.parametrize("helper", HELPERS)
def test_e2e_deploy_helpers_resolve_repo_root(helper: Path) -> None:
    source = helper.read_text(encoding="utf-8")

    assert find_repo_root(helper) == REPO_ROOT
    assert "find_repo_root(__file__)" in source
    assert "parents[3]" not in source


@pytest.mark.parametrize(
    "asset_path",
    [
        "tests/shared/external_vendor_api",
        "tests/shared/infrastructure_code/alloy_config/Dockerfile",
        "tests/e2e/upstream_lambda/pipeline_code",
        "tests/e2e/upstream_prefect_ecs_fargate/infrastructure_code/"
        "prefect_image/Dockerfile",
        "tests/e2e/upstream_prefect_ecs_fargate/pipeline_code/trigger_lambda",
        "tests/e2e/upstream_apache_flink_ecs/infrastructure_code/"
        "flink_image/Dockerfile",
        "tests/e2e/upstream_apache_flink_ecs/pipeline_code/trigger_lambda",
    ],
)
def test_e2e_deploy_asset_paths_exist(asset_path: str) -> None:
    assert (REPO_ROOT / asset_path).exists()
