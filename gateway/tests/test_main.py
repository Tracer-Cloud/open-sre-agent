"""Tests for the package entry ``gateway.main``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_gateway_main_delegates_to_manager() -> None:
    """``python -m gateway.main`` must boot ``GatewayManager.start_gateway``."""
    mock_manager = MagicMock()
    with patch("gateway.runtime.manager.GatewayManager", return_value=mock_manager):
        from gateway.runtime.manager import main as runtime_main

        runtime_main()

    mock_manager.start_gateway.assert_called_once_with()


def test_gateway_main_module_exports_main() -> None:
    import gateway.main as entry

    assert callable(entry.main)
    assert entry.main.__module__ == "gateway.runtime.manager"
