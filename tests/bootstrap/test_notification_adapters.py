"""The notification registration step: every channel, from one place only.

An empty registry is not a loud failure. Every configured channel simply reports
"unsupported", which reads to a user exactly like a channel name typo, so these
tests pin that the step registers all four and that nothing else registers them.
"""

from __future__ import annotations

import ast
import pathlib

from bootstrap.adapters import install_notification_adapters
from infrastructure.delivery.notifications.outbound_registry import (
    BACKGROUND_RCA,
    clear_outbound_adapters,
    get_outbound_adapter,
    outbound_adapter_names_for,
)

_EXPECTED_CHANNELS = ("buzz", "email", "rocketchat", "telegram")


def test_step_registers_every_shipped_channel() -> None:
    names = install_notification_adapters()

    assert sorted(names) == list(_EXPECTED_CHANNELS)


def test_step_is_idempotent() -> None:
    """The dispatcher calls it on every completion, so repeat calls must not
    duplicate or drop adapters."""
    first = sorted(install_notification_adapters())
    second = sorted(install_notification_adapters())

    assert first == second == list(_EXPECTED_CHANNELS)


def test_step_repopulates_a_cleared_registry() -> None:
    """Calling it twice proves nothing on its own: the second call is a no-op
    against an already-full registry either way. Registration has to survive a
    clear, because the step registers adapter objects rather than relying on the
    module import side effect, and imports are cached. Relying on the import
    alone leaves the registry empty here while the step reports success, which
    would make ``/background notify set email`` reject its own shipped channel."""
    clear_outbound_adapters()

    names = install_notification_adapters()

    assert sorted(names) == list(_EXPECTED_CHANNELS)
    assert get_outbound_adapter("telegram") is not None


def test_every_registered_channel_advertises_background_rca() -> None:
    install_notification_adapters()

    assert outbound_adapter_names_for(BACKGROUND_RCA) == _EXPECTED_CHANNELS


def test_each_adapter_reports_its_own_name() -> None:
    """A copy-paste slip in an adapter's ``name`` would register it under the
    wrong channel, which no dispatch test would catch."""
    install_notification_adapters()

    for channel in _EXPECTED_CHANNELS:
        adapter = get_outbound_adapter(channel)
        assert adapter is not None, channel
        assert adapter.name == channel


def test_only_the_composition_root_registers_notification_adapters() -> None:
    # Arrange: the point of the composition root is that registration lives in
    # exactly one place. A second loader elsewhere would drift, and a comment
    # asking people not to add one is not enforcement.
    repo = pathlib.Path(__file__).resolve().parents[2]
    packages = (
        "bootstrap",
        "surfaces",
        "gateway",
        "tools",
        "integrations",
        "infrastructure",
        "core",
    )

    # Act
    definers = sorted(
        f"{path.relative_to(repo)}::{node.name}"
        for package in packages
        for path in (repo / package).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "install_notification_adapters"
    )

    # Assert
    assert definers == ["bootstrap/adapters.py::install_notification_adapters"], definers
