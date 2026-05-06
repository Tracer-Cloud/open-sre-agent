from __future__ import annotations

from pathlib import Path

from tests.e2e.upstream_apache_flink_ecs.infrastructure_sdk import deploy as flink_deploy
from tests.e2e.upstream_lambda.infrastructure_sdk import deploy as lambda_deploy
from tests.e2e.upstream_prefect_ecs_fargate.infrastructure_sdk import deploy as prefect_deploy

EXPECTED_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_upstream_lambda_deploy_paths_exist() -> None:
    assert lambda_deploy.REPO_ROOT == EXPECTED_REPO_ROOT
    assert lambda_deploy.MOCK_API_CODE_DIR.is_dir()
    assert lambda_deploy.PIPELINE_CODE_DIR.is_dir()


def test_upstream_prefect_deploy_paths_exist() -> None:
    assert prefect_deploy.REPO_ROOT == EXPECTED_REPO_ROOT
    assert prefect_deploy.PREFECT_DOCKERFILE.is_file()
    assert prefect_deploy.ALLOY_CONFIG_DIR.is_dir()
    assert prefect_deploy.MOCK_API_CODE_DIR.is_dir()
    assert prefect_deploy.TRIGGER_LAMBDA_CODE_DIR.is_dir()


def test_upstream_flink_deploy_paths_exist() -> None:
    assert flink_deploy.REPO_ROOT == EXPECTED_REPO_ROOT
    assert flink_deploy.FLINK_DOCKERFILE.is_file()
    assert flink_deploy.MOCK_API_CODE_DIR.is_dir()
    assert flink_deploy.TRIGGER_LAMBDA_CODE_DIR.is_dir()
