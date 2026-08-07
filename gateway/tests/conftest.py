"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from config.platform_bootstrap import ensure_project_platform_package

ensure_project_platform_package()


@pytest.fixture(autouse=True)
def _isolate_gateway_runtime_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every gateway test off the developer's real ``~/.opensre/gateway``.

    ``GatewayManager.stop()`` clears the component status file, and
    ``start_gateway`` rewrites the pidfile. Both resolve through module globals
    captured at import time, so the root ``OPENSRE_HOME_DIR`` override does not
    reach them: a unit test that merely constructs a manager and stops it
    deleted the status file of the daemon actually running on the machine,
    leaving ``opensre gateway status`` blank until the next restart.
    """
    from gateway.core.runtime import daemon
    from gateway.transports.buzz.poller import cursor as buzz_cursor

    runtime_dir = tmp_path / "gateway"
    monkeypatch.setattr(daemon, "GATEWAY_PID_FILE", runtime_dir / "gateway.pid")
    monkeypatch.setattr(daemon, "GATEWAY_LOG_FILE", runtime_dir / "gateway.log")
    monkeypatch.setattr(daemon, "GATEWAY_COMPONENTS_FILE", runtime_dir / "components.json")
    monkeypatch.setattr(buzz_cursor, "BUZZ_CURSOR_FILE", runtime_dir / "buzz_cursor.json")


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
