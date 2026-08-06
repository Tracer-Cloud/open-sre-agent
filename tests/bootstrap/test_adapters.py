"""Registration steps — one implementation each, fixed order within a step.

``surfaces`` and ``gateway`` may not import each other, so each used to keep a
byte-identical copy of this registration synchronised by a docstring comment.
The composition root owns it now; the last test is what keeps a second copy from
reappearing.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from bootstrap import adapters

_REGISTRARS = {
    "integrations.harness_adapters.register_harness_adapters": "integrations",
    "tools.harness_adapters.register_harness_adapters": "tools",
    "tools.investigation.scheduler_bootstrap.install": "investigation",
    "integrations.scheduled_agent_bootstrap.install": "scheduled",
}
_STEPS = ("install_harness_adapters", "install_scheduler_runners")


@pytest.fixture
def registration_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which registrars ran, in order, without importing their bodies."""
    calls: list[str] = []

    def _recorder(name: str) -> Any:
        def _record() -> None:
            calls.append(name)

        return _record

    for target, name in _REGISTRARS.items():
        monkeypatch.setattr(target, _recorder(name))
    monkeypatch.setattr(
        "bootstrap.adapters.install_investigation_api",
        _recorder("investigation_api"),
    )
    return calls


def test_harness_adapters_registers_both_registries(registration_calls: list[str]) -> None:
    # Arrange / Act
    adapters.install_harness_adapters()

    # Assert: adapters include the AgentSession.investigate payload runner;
    # they must not silently pull in scheduler runners.
    assert registration_calls == ["integrations", "tools", "investigation_api"]


def test_scheduler_runners_registers_investigation_first(registration_calls: list[str]) -> None:
    # Arrange / Act
    adapters.install_scheduler_runners()

    # Assert: the scheduled-agent runner resolves against the investigation one,
    # so the order is load-bearing, not cosmetic.
    assert registration_calls == ["investigation", "scheduled"]


def test_steps_compose_without_a_flag_argument(registration_calls: list[str]) -> None:
    # Arrange / Act: a host needing both calls both — there is no mode
    # parameter to get wrong.
    adapters.install_harness_adapters()
    adapters.install_scheduler_runners()

    # Assert
    assert registration_calls == [
        "integrations",
        "tools",
        "investigation_api",
        "investigation",
        "scheduled",
    ]


def test_only_the_composition_root_defines_the_steps() -> None:
    # Arrange: a second copy is what this refactor removed. surfaces and gateway
    # cannot import each other, so pressure to re-add one is permanent — and a
    # comment asking people to "keep them in sync" is not enforcement.
    repo = pathlib.Path(__file__).resolve().parents[2]
    packages = ("bootstrap", "surfaces", "gateway", "tools", "integrations", "platform", "core")

    # Act
    definers = sorted(
        f"{path.relative_to(repo)}::{node.name}"
        for package in packages
        for path in (repo / package).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name in _STEPS
    )

    # Assert
    assert definers == [
        "bootstrap/adapters.py::install_harness_adapters",
        "bootstrap/adapters.py::install_scheduler_runners",
    ], definers
