"""Regression test for #1418 — e2e deploy helpers resolve repo root + asset paths.

The three AWS deploy helpers under

    tests/e2e/upstream_lambda/infrastructure_sdk/deploy.py
    tests/e2e/upstream_prefect_ecs_fargate/infrastructure_sdk/deploy.py
    tests/e2e/upstream_apache_flink_ecs/infrastructure_sdk/deploy.py

each compute ``project_root = Path(__file__).resolve().parents[N]``. They live
four directory levels deep, so the correct depth is ``parents[4]``. A previous
version used ``parents[3]``, which resolved to ``<repo>/tests`` and broke every
asset path built from it (``tests/tests/shared/...``, ``tests/tests/upstream_*/...``).

This test does NOT import the deploy modules (their AWS-deploy dependencies are
heavy and live-infrastructure-only). Instead it:

- parses the ``parents[N]`` literal out of each deploy.py and asserts the
  resolved directory equals the actual repository root;
- asserts the asset paths the helpers reference actually exist on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SCENARIOS = (
    "upstream_lambda",
    "upstream_prefect_ecs_fargate",
    "upstream_apache_flink_ecs",
)


def _deploy_py(scenario: str) -> Path:
    return REPO_ROOT / "tests" / "e2e" / scenario / "infrastructure_sdk" / "deploy.py"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_deploy_project_root_resolves_to_repo_root(scenario: str) -> None:
    deploy_py = _deploy_py(scenario)
    assert deploy_py.is_file(), f"missing deploy helper: {deploy_py}"

    source = deploy_py.read_text(encoding="utf-8")
    match = re.search(
        r"project_root\s*=\s*Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]",
        source,
    )
    assert match, (
        f"{deploy_py.relative_to(REPO_ROOT)}: expected a "
        "`project_root = Path(__file__).resolve().parents[N]` declaration"
    )

    depth = int(match.group(1))
    resolved = deploy_py.resolve().parents[depth]
    assert resolved == REPO_ROOT, (
        f"{deploy_py.relative_to(REPO_ROOT)}: parents[{depth}] resolves to "
        f"{resolved}, expected the repo root {REPO_ROOT}. The deploy helper is "
        "four directories deep — use parents[4]."
    )


def test_shared_assets_exist() -> None:
    """Assets the deploy helpers resolve from `tests/shared/...`.

    `external_vendor_api` is bundled as a Lambda by all three helpers
    (the mock external vendor API). `infrastructure_code/alloy_config`
    is only read by the Prefect helper's `ALLOY_CONFIG_DIR`; the Flink
    helper embeds an equivalent Alloy config inline and the Lambda
    helper has no Alloy sidecar. The directory is still checked here
    because it is the canonical location for that config and the
    Prefect helper's resolution depends on the same `tests/shared/`
    layout this test guards.
    """
    for rel in (
        Path("external_vendor_api"),
        Path("infrastructure_code") / "alloy_config",
    ):
        path = REPO_ROOT / "tests" / "shared" / rel
        assert path.exists(), f"shared asset missing: {path.relative_to(REPO_ROOT)}"


@pytest.mark.parametrize(
    ("scenario", "rel_asset"),
    [
        ("upstream_lambda", "pipeline_code"),
        (
            "upstream_prefect_ecs_fargate",
            "infrastructure_code/prefect_image/Dockerfile",
        ),
        ("upstream_prefect_ecs_fargate", "pipeline_code/trigger_lambda"),
        (
            "upstream_apache_flink_ecs",
            "infrastructure_code/flink_image/Dockerfile",
        ),
        ("upstream_apache_flink_ecs", "pipeline_code/trigger_lambda"),
    ],
)
def test_scenario_assets_exist_under_tests_e2e(scenario: str, rel_asset: str) -> None:
    """Scenario-specific assets must live under `tests/e2e/<scenario>/...`."""
    path = REPO_ROOT / "tests" / "e2e" / scenario / rel_asset
    assert path.exists(), (
        f"asset missing under tests/e2e/{scenario}/{rel_asset}: "
        f"{path.relative_to(REPO_ROOT)}"
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_deploy_uses_tests_e2e_for_scenario_assets(scenario: str) -> None:
    """Guard against re-introducing `tests/<scenario>/...` paths missing `e2e/`.

    The deploy helpers must reference scenario-specific assets at
    ``tests/e2e/<scenario>/...``. The previous (buggy) layout used
    ``tests/<scenario>/...``, which only resolved because the wrong
    ``parents[3]`` doubled the leading ``tests/`` segment.
    """
    deploy_py = _deploy_py(scenario)
    source = deploy_py.read_text(encoding="utf-8")

    pattern = rf'["\']tests/{re.escape(scenario)}/'
    offenders = re.findall(pattern, source)
    assert not offenders, (
        f"{deploy_py.relative_to(REPO_ROOT)}: scenario-specific asset paths "
        f"must use 'tests/e2e/{scenario}/...', not 'tests/{scenario}/...'."
    )

    quoted_segment_pattern = rf'["\']{re.escape(scenario)}["\']'
    quoted_segments = re.findall(quoted_segment_pattern, source)
    if quoted_segments:
        e2e_quoted = re.findall(r'["\']e2e["\']', source)
        assert e2e_quoted, (
            f"{deploy_py.relative_to(REPO_ROOT)}: references "
            f"{quoted_segments[0]} as a path segment without a sibling 'e2e' "
            "segment — the scenario directory lives under tests/e2e/."
        )
