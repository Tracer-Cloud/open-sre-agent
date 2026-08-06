"""Characterization for surfaces/gateway ``install_runtime`` composition roots.

``tools`` and ``integrations`` are sibling layers — the flag matrix must live
in Tier-1 packages. Surfaces and gateway each keep a peer copy; both are
covered here so they stay in sync.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.core.runtime import bootstrap as gateway_bootstrap
from surfaces.shared import runtime_bootstrap as surfaces_bootstrap


@pytest.mark.parametrize(
    "module",
    [surfaces_bootstrap, gateway_bootstrap],
    ids=["surfaces", "gateway"],
)
def test_install_runtime_adapters_only(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "integrations.harness_adapters.register_harness_adapters",
        lambda: calls.append("integrations"),
    )
    monkeypatch.setattr(
        "tools.harness_adapters.register_harness_adapters",
        lambda: calls.append("tools"),
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.install",
        lambda: calls.append("scheduled"),
    )
    monkeypatch.setattr(
        "tools.investigation.scheduler_bootstrap.install",
        lambda: calls.append("investigation"),
    )

    module.install_runtime(harness_adapters=True, scheduler_runners=False)

    assert calls == ["integrations", "tools"]


@pytest.mark.parametrize(
    "module",
    [surfaces_bootstrap, gateway_bootstrap],
    ids=["surfaces", "gateway"],
)
def test_install_runtime_runners_only(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "integrations.harness_adapters.register_harness_adapters",
        lambda: calls.append("integrations"),
    )
    monkeypatch.setattr(
        "tools.harness_adapters.register_harness_adapters",
        lambda: calls.append("tools"),
    )
    monkeypatch.setattr(
        "integrations.scheduled_agent_bootstrap.install",
        lambda: calls.append("scheduled"),
    )
    monkeypatch.setattr(
        "tools.investigation.scheduler_bootstrap.install",
        lambda: calls.append("investigation"),
    )

    module.install_runtime(harness_adapters=False, scheduler_runners=True)

    assert calls == ["investigation", "scheduled"]


@pytest.mark.parametrize(
    "module",
    [surfaces_bootstrap, gateway_bootstrap],
    ids=["surfaces", "gateway"],
)
def test_install_runtime_full_set(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _track(name: str) -> Any:
        return lambda: calls.append(name)

    monkeypatch.setattr(
        "integrations.harness_adapters.register_harness_adapters", _track("integrations")
    )
    monkeypatch.setattr("tools.harness_adapters.register_harness_adapters", _track("tools"))
    monkeypatch.setattr("tools.investigation.scheduler_bootstrap.install", _track("investigation"))
    monkeypatch.setattr("integrations.scheduled_agent_bootstrap.install", _track("scheduled"))

    module.install_runtime()

    assert calls == ["integrations", "tools", "investigation", "scheduled"]
