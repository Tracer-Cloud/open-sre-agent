"""Regression coverage for repo-root resolution in e2e infrastructure deploy helpers.

These deploy modules build Lambda zips and Docker contexts from on-disk asset
directories. Drifting the `parents[N]` index (or re-introducing a `tests/`
double-prefix) silently breaks the bundling step at `iterdir()` time, well
after CI has spent minutes provisioning cloud resources. The checks below
catch that locally.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DEPLOY_HELPERS: list[tuple[str, Path, list[str]]] = [
    (
        "upstream_lambda",
        REPO_ROOT / "tests/e2e/upstream_lambda/infrastructure_sdk/deploy.py",
        [
            "tests/shared/external_vendor_api",
            "tests/e2e/upstream_lambda/pipeline_code",
        ],
    ),
    (
        "upstream_prefect_ecs_fargate",
        REPO_ROOT / "tests/e2e/upstream_prefect_ecs_fargate/infrastructure_sdk/deploy.py",
        [
            "tests/shared/external_vendor_api",
            "tests/shared/infrastructure_code/alloy_config",
            "tests/e2e/upstream_prefect_ecs_fargate/infrastructure_code/prefect_image/Dockerfile",
            "tests/e2e/upstream_prefect_ecs_fargate/pipeline_code/trigger_lambda",
        ],
    ),
    (
        "upstream_apache_flink_ecs",
        REPO_ROOT / "tests/e2e/upstream_apache_flink_ecs/infrastructure_sdk/deploy.py",
        [
            "tests/shared/external_vendor_api",
            "tests/e2e/upstream_apache_flink_ecs/infrastructure_code/flink_image/Dockerfile",
            "tests/e2e/upstream_apache_flink_ecs/pipeline_code/trigger_lambda",
        ],
    ),
]


def _load_deploy_module(scenario: str, deploy_file: Path) -> ModuleType:
    module_name = f"_deploy_helper_under_test_{scenario}"
    spec = importlib.util.spec_from_file_location(module_name, deploy_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


@pytest.mark.parametrize(
    ("scenario", "deploy_file", "expected_assets"),
    DEPLOY_HELPERS,
    ids=[scenario for scenario, _, _ in DEPLOY_HELPERS],
)
def test_deploy_helper_resolves_repo_root(
    scenario: str, deploy_file: Path, expected_assets: list[str]
) -> None:
    assert deploy_file.exists(), f"deploy helper missing: {deploy_file}"
    module = _load_deploy_module(scenario, deploy_file)

    assert module.project_root == REPO_ROOT, (
        f"{deploy_file} resolves project_root to {module.project_root}, expected {REPO_ROOT}"
    )

    for relative in expected_assets:
        asset = REPO_ROOT / relative
        assert asset.exists(), f"{scenario}: expected asset path missing on disk: {asset}"
