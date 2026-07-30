"""Characterization for :func:`tools.runtime_bootstrap.install_runtime`."""

from __future__ import annotations

from typing import Any

import pytest

from tools import runtime_bootstrap


def test_install_runtime_adapters_only(monkeypatch: pytest.MonkeyPatch) -> None:
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

    runtime_bootstrap.install_runtime(harness_adapters=True, scheduler_runners=False)

    assert calls == ["integrations", "tools"]


def test_install_runtime_runners_only(monkeypatch: pytest.MonkeyPatch) -> None:
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

    runtime_bootstrap.install_runtime(harness_adapters=False, scheduler_runners=True)

    assert calls == ["investigation", "scheduled"]


def test_install_runtime_full_set(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _track(name: str) -> Any:
        return lambda: calls.append(name)

    monkeypatch.setattr(
        "integrations.harness_adapters.register_harness_adapters", _track("integrations")
    )
    monkeypatch.setattr("tools.harness_adapters.register_harness_adapters", _track("tools"))
    monkeypatch.setattr("tools.investigation.scheduler_bootstrap.install", _track("investigation"))
    monkeypatch.setattr("integrations.scheduled_agent_bootstrap.install", _track("scheduled"))

    runtime_bootstrap.install_runtime()

    assert calls == ["integrations", "tools", "investigation", "scheduled"]
