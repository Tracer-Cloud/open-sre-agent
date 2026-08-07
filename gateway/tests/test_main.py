"""Tests for the package entry ``gateway.main``."""

from __future__ import annotations

import pytest


def test_gateway_main_fails_closed_without_slash_ports() -> None:
    """Bare ``python -m gateway.main`` must not boot a slash-less gateway."""
    import gateway.main as entry

    with pytest.raises(SystemExit, match="slash-port glue"):
        entry.main()


def test_manager_main_fails_closed_without_slash_ports() -> None:
    from gateway.core.runtime import manager as manager_module

    with pytest.raises(SystemExit, match="slash_ports_factory"):
        manager_module.main()


def test_manager_start_gateway_wrapper_requires_slash_ports() -> None:
    from gateway.core.runtime.manager import start_gateway

    with pytest.raises(SystemExit, match="slash_ports_factory"):
        start_gateway(wait=False)


def test_gateway_main_module_exports_main() -> None:
    import gateway.main as entry

    assert callable(entry.main)
    assert entry.main.__module__ == "gateway.main"
