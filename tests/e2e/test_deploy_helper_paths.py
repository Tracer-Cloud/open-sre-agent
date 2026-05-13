"""Regression tests for E2E deploy helper repository-root paths."""

from __future__ import annotations

from tests.e2e.upstream_apache_flink_ecs.infrastructure_sdk import deploy as flink_deploy
from tests.e2e.upstream_lambda.infrastructure_sdk import deploy as lambda_deploy
from tests.e2e.upstream_prefect_ecs_fargate.infrastructure_sdk import deploy as prefect_deploy


def test_e2e_deploy_helpers_resolve_repo_root() -> None:
    for module in (lambda_deploy, prefect_deploy, flink_deploy):
        assert (module.project_root / "pyproject.toml").exists()
        assert module.project_root.name == "opensre"


def test_upstream_lambda_asset_paths_exist() -> None:
    root = lambda_deploy.project_root
    assert (root / "tests" / "shared" / "external_vendor_api").exists()
    assert (root / "tests" / "e2e" / "upstream_lambda" / "pipeline_code").exists()


def test_upstream_prefect_asset_paths_exist() -> None:
    assert prefect_deploy.MOCK_API_CODE.exists()
    assert prefect_deploy.TRIGGER_LAMBDA_CODE.exists()
    assert prefect_deploy.PREFECT_DOCKERFILE.exists()


def test_upstream_flink_asset_paths_exist() -> None:
    root = flink_deploy.project_root
    assert (
        root
        / "tests"
        / "e2e"
        / "upstream_apache_flink_ecs"
        / "infrastructure_code"
        / "flink_image"
        / "Dockerfile"
    ).exists()
    assert (
        root / "tests" / "e2e" / "upstream_apache_flink_ecs" / "pipeline_code" / "trigger_lambda"
    ).exists()
