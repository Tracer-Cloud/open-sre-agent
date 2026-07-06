"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()


@pytest.fixture(autouse=True)
def _harness_ports_per_test() -> Iterator[None]:
    """Wire harness ports before each test; reset after to avoid session leakage."""
    from platform.harness_ports import reset_harness_ports
    from surfaces.interactive_shell.ui.output.boundary import install_harness_ports

    install_harness_ports()
    yield
    reset_harness_ports()
