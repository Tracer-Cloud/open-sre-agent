"""Boot-time capability probe.

The agent is told it can run Python, read files, and reach the network. When the
environment silently withholds one of those, the failure surfaces mid-turn as a
confusing answer rather than at startup. Probing once at boot turns "the agent is
oddly limited" into a warning that names the missing capability.

Presence on PATH is a different question, already covered by
``config.runtime_metadata.probes``: a binary can exist and still be unusable.
These tests pin what the probe reports and, more importantly, that it never
raises — a diagnostic that crashes startup is worse than no diagnostic.
"""

from __future__ import annotations

from typing import Any

from platform.sandbox.capabilities import (
    Capability,
    probe_capabilities,
    unavailable_capability_warnings,
)


def test_python_execution_is_detected() -> None:
    # Arrange / Act
    results = probe_capabilities()

    # Assert
    assert results[Capability.PYTHON].available is True


def test_file_read_is_detected() -> None:
    results = probe_capabilities()
    assert results[Capability.FILE_READ].available is True


def test_every_capability_is_reported() -> None:
    """A missing key would silently drop a capability from the warning list."""
    results = probe_capabilities()
    assert set(results) == set(Capability)


def test_warnings_name_only_the_unavailable_ones() -> None:
    # Arrange: one capability reported unavailable.
    results = probe_capabilities()
    results[Capability.NETWORK] = results[Capability.NETWORK].__class__(
        capability=Capability.NETWORK, available=False, detail="blocked by policy"
    )

    # Act
    warnings = unavailable_capability_warnings(results)

    # Assert
    assert len(warnings) == 1
    assert "network" in warnings[0].lower()
    assert "blocked by policy" in warnings[0]


def test_no_warnings_when_everything_works() -> None:
    results = probe_capabilities()
    for name in list(results):
        results[name] = results[name].__class__(capability=name, available=True, detail="")
    assert unavailable_capability_warnings(results) == []


def test_probe_never_raises_even_when_the_check_explodes(monkeypatch: Any) -> None:
    """A diagnostic must not be able to take down startup."""
    # Arrange
    import platform.sandbox.capabilities as capabilities

    def _boom(*_args: Any, **_kwargs: Any) -> bool:
        raise OSError("environment is hostile")

    monkeypatch.setattr(capabilities, "_python_available", _boom)

    # Act
    results = probe_capabilities()

    # Assert: reported unavailable with the reason, not propagated.
    assert results[Capability.PYTHON].available is False
    assert "hostile" in results[Capability.PYTHON].detail


def test_network_probe_does_not_make_a_real_request(monkeypatch: Any) -> None:
    """Startup must not depend on, or wait for, an external host."""
    # Arrange
    import socket

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("capability probe attempted a real connection")

    monkeypatch.setattr(socket, "create_connection", _fail)

    # Act / Assert — no exception means no outbound call was attempted.
    probe_capabilities()
