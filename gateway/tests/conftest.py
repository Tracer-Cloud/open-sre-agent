"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()

# Gateway modules that transitively import the private opensre-infra-aws
# submodule (SizeProfile / control-plane ports). Skip collecting these when
# the submodule is not checked out so fork/community CI stays green.
_OPENSRE_INFRA_AWS_GATEWAY_TEST_SUFFIXES = (
    "gateway/tests/main/test_agent_life_cycle.py",
    "gateway/tests/runtime/test_concurrency_gate.py",
    "gateway/tests/runtime/test_credential_hydration.py",
    "gateway/tests/runtime/test_manager.py",
    "gateway/tests/runtime/test_remote_run_worker.py",
    "gateway/tests/runtime/test_slash_routing.py",
    "gateway/tests/test_main.py",
)

_OPENSRE_INFRA_AWS_AVAILABLE = (
    find_spec("platform.deployment_multi_tenant.lambda_control_plane") is not None
)

if not _OPENSRE_INFRA_AWS_AVAILABLE:
    # Relative to this conftest — covers directory discovery.
    collect_ignore_glob = [
        "main/test_agent_life_cycle.py",
        "runtime/test_concurrency_gate.py",
        "runtime/test_credential_hydration.py",
        "runtime/test_manager.py",
        "runtime/test_remote_run_worker.py",
        "runtime/test_slash_routing.py",
        "test_main.py",
    ]


def pytest_configure(config: pytest.Config) -> None:
    """Drop explicit CLI paths that need the private submodule when it is absent."""
    if _OPENSRE_INFRA_AWS_AVAILABLE:
        return
    kept: list[str] = []
    for arg in config.args:
        normalized = Path(arg).as_posix()
        if any(normalized.endswith(suffix) for suffix in _OPENSRE_INFRA_AWS_GATEWAY_TEST_SUFFIXES):
            continue
        kept.append(arg)
    config.args = kept


@pytest.fixture(autouse=True)
def _harness_ports_per_test() -> Iterator[None]:
    """Wire harness ports before each test; reset after to avoid session leakage.

    Registers the tools and integrations adapters directly (the same pair
    ``install_harness_ports`` wires) so the gateway package stays below
    ``surfaces`` in the import layering.
    """
    from integrations.harness_adapters import register_harness_adapters as register_integrations
    from platform.harness_ports import reset_harness_ports
    from tools.harness_adapters import register_harness_adapters as register_tools

    register_integrations()
    register_tools()
    yield
    reset_harness_ports()
