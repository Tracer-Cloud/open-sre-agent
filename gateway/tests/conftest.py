"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gateway.core.process import component_status, supervision


@pytest.fixture(autouse=True)
def _isolate_opensre_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every gateway test off the developer's real ``~/.opensre``.

    Patch the attribute, not ``OPENSRE_HOME``: that env var is read once at
    import, while the root helpers re-read the attribute per call.

    Disabling the keyring is not optional here. Redirecting the home makes
    credential lookup miss ``integrations.json`` and fall through to the OS
    keychain, which blocks on a GUI prompt, so the pair must move together the
    way ``tests/conftest.py`` keeps them.
    """
    from config.constants import paths
    from config.secrets.os_keyring import reset_keyring_state

    reset_keyring_state()
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "opensre-home")


@pytest.fixture(autouse=True)
def _isolate_gateway_runtime_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every gateway test off the developer's real ``~/.opensre/gateway``.

    ``GatewayController.stop()`` clears the component status file, and
    ``start_gateway`` rewrites the pidfile. Both resolve through module globals
    captured at import time, so the root ``OPENSRE_HOME_DIR`` override does not
    reach them: a unit test that merely constructs a manager and stops it
    deleted the status file of the daemon actually running on the machine,
    leaving ``opensre gateway status`` blank until the next restart.
    """
    from gateway.transports.buzz.poller import cursor as buzz_cursor

    runtime_dir = tmp_path / "gateway"
    monkeypatch.setattr(supervision, "GATEWAY_PID_FILE", runtime_dir / "gateway.pid")
    monkeypatch.setattr(supervision, "GATEWAY_LOG_FILE", runtime_dir / "gateway.log")
    monkeypatch.setattr(
        component_status, "GATEWAY_COMPONENTS_FILE", runtime_dir / "components.json"
    )
    monkeypatch.setattr(buzz_cursor, "BUZZ_CURSOR_FILE", runtime_dir / "buzz_cursor.json")


@pytest.fixture(autouse=True)
def _harness_ports_per_test() -> Iterator[None]:
    """Register harness ports before each test; reset after to avoid session leakage.

    Registers the tools and integrations adapters directly (the same pair
    ``install_harness_ports`` registers) so the gateway package stays below
    ``surfaces`` in the import layering.
    """
    from infrastructure.harness_ports import reset_harness_ports
    from integrations.harness_adapters import register_harness_adapters as register_integrations
    from tools.harness_adapters import register_harness_adapters as register_tools

    register_integrations()
    register_tools()
    yield
    reset_harness_ports()
