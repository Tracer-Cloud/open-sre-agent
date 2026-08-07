"""Gateway process boot: one named step, in a fixed order.

``start_gateway`` opened with thirteen statements of process setup — harness
construction, env resolution, adapter registration, Sentry, capability warnings,
LLM preload — before any gateway-specific work. None of it is gateway logic, and
the ordering constraints between the steps were documented only as inline
comments inside a lifecycle method.

The order is load-bearing and is what these tests pin:

* env variables resolve **before** adapters register, because adapter/integration
  hydration can depend on env-provided credentials;
* the LLM client graph preloads **after** adapters, so the snapshot sees the
  registered set;
* capability warnings are emitted at startup, before the first mid-turn failure can
  present as a confusing answer.
"""

from __future__ import annotations

import logging
from typing import Any


class _StubHarness:
    """Stands in for :class:`AgentHarness`; records that env resolution ran."""

    def __init__(self, order: list[str] | None = None) -> None:
        self._order = order

    def resolve_env_variables(self) -> None:
        if self._order is not None:
            self._order.append("env")


def test_startup_runs_the_steps_in_the_required_order(monkeypatch: Any) -> None:
    # Arrange
    from gateway.core.runtime import startup

    order: list[str] = []
    monkeypatch.setattr(startup, "install_runtime", lambda **_kw: order.append("adapters"))
    monkeypatch.setattr(startup, "init_sentry", lambda **_kw: order.append("sentry"))
    monkeypatch.setattr(startup, "preload_llm_clients", lambda: order.append("preload"))
    monkeypatch.setattr(startup, "boot_capability_warnings", lambda: ["curl missing"])

    monkeypatch.setattr(startup, "_build_harness", lambda: _StubHarness(order))

    # Act
    startup.run(logging.getLogger("test.startup"))

    # Assert
    assert order.index("env") < order.index("adapters"), order
    assert order.index("adapters") < order.index("preload"), order


def test_startup_logs_every_capability_warning(monkeypatch: Any) -> None:
    """A missing capability must surface at startup, not mid-turn."""
    # Arrange
    from gateway.core.runtime import startup

    monkeypatch.setattr(startup, "install_runtime", lambda **_kw: None)
    monkeypatch.setattr(startup, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(startup, "preload_llm_clients", lambda: None)
    monkeypatch.setattr(
        startup, "boot_capability_warnings", lambda: ["curl is not on PATH", "network blocked"]
    )
    monkeypatch.setattr(startup, "_build_harness", _StubHarness)

    logged: list[str] = []

    class _Logger(logging.Logger):
        def __init__(self) -> None:
            super().__init__("test.startup")

        def warning(self, msg: str, *args: Any, **_kw: Any) -> None:
            logged.append(msg % args if args else msg)

    # Act
    startup.run(_Logger())

    # Assert
    assert any("curl is not on PATH" in line for line in logged), logged
    assert any("network blocked" in line for line in logged), logged


def test_startup_returns_the_harness_for_the_caller(monkeypatch: Any) -> None:
    """The gateway keeps the harness it booted; boot does not hide it."""
    from gateway.core.runtime import startup

    sentinel = _StubHarness()
    monkeypatch.setattr(startup, "install_runtime", lambda **_kw: None)
    monkeypatch.setattr(startup, "init_sentry", lambda **_kw: None)
    monkeypatch.setattr(startup, "preload_llm_clients", lambda: None)
    monkeypatch.setattr(startup, "boot_capability_warnings", lambda: [])
    monkeypatch.setattr(startup, "_build_harness", lambda: sentinel)

    assert startup.run(logging.getLogger("test.startup")) is sentinel
