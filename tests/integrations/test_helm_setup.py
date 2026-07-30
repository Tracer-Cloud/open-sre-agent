"""Interactive setup coverage for the Helm integration.

Prompt order, defaults, and persistence to the keyring/``.env``/store are
covered generically for every spec-backed integration (including Helm) in
:mod:`tests.integrations.test_cli_spec_setup`; this file only pins the
handler's registration.
"""

from __future__ import annotations

from unittest.mock import patch

from integrations.cli import cmd_setup
from integrations.registry import SUPPORTED_SETUP_SERVICES


def test_cmd_setup_helm_dispatches_handler() -> None:
    calls: list[str] = []

    def fake_handler() -> None:
        calls.append("helm")

    with patch.dict("integrations.cli._HANDLERS", {"helm": fake_handler}):
        resolved = cmd_setup("helm")

    assert resolved == "helm"
    assert calls == ["helm"]


def test_helm_is_registered_for_setup() -> None:
    from integrations.cli import _HANDLERS

    assert "helm" in SUPPORTED_SETUP_SERVICES
    assert "helm" in _HANDLERS
