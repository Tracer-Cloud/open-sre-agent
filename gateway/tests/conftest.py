"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()


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
