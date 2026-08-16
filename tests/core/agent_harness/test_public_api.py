"""The harness's three import doors are pinned exactly.

* ``core.agent_harness`` — public API for embedders.
* ``core.agent_harness.spi`` — what a host consumes to integrate a turn; import-cheap.
* ``core.agent_harness.runtime`` — build and run the agent; loads ``core.agent``.

Adding a name to any door is an API change made here.
"""

from __future__ import annotations

import subprocess
import sys

import core.agent_harness as root
import core.agent_harness.runtime as runtime
import core.agent_harness.spi as spi

PUBLIC_API = frozenset(
    {"AgentSession", "SessionConfig", "SessionCore", "SessionManager", "OutputSink"}
)

SPI = frozenset(
    {
        "ChatTurnBindings",
        "DefaultPromptContextProvider",
        "DefaultTurnAccounting",
        "SessionGoal",
        "ToolCallingTurnResult",
        "TurnResult",
        "dispatch_chat_turn",
        "ensure_turn_cancel",
        "format_session_goal_progress",
        "format_session_goal_status_line",
        "has_resolved_integrations",
        "run_until_session_goal",
        "stream_answer",
    }
)

RUNTIME = frozenset(
    {
        "ActionTurnRunner",
        "GatherPorts",
        "HeadlessAgent",
        "ToolCallingDeps",
        "build_default_headless_agent",
    }
)


def test_root_exports_exactly_the_public_api() -> None:
    assert set(root.__all__) == PUBLIC_API


def test_spi_exports_exactly_its_list() -> None:
    assert set(spi.__all__) == SPI


def test_runtime_exports_exactly_its_list() -> None:
    assert set(runtime.__all__) == RUNTIME


def test_the_three_doors_do_not_overlap() -> None:
    assert not (PUBLIC_API & SPI)
    assert not (PUBLIC_API & RUNTIME)
    assert not (SPI & RUNTIME)


def test_every_name_resolves_through_its_door() -> None:
    for name in PUBLIC_API:
        assert getattr(root, name) is not None
    for name in SPI:
        assert getattr(spi, name) is not None
    for name in RUNTIME:
        assert getattr(runtime, name) is not None


def test_the_root_has_no_lazy_attribute_machinery() -> None:
    assert not hasattr(root, "_LAZY_EXPORTS")
    assert "__getattr__" not in vars(root)


def test_root_and_spi_do_not_load_the_agent_loop() -> None:
    code = (
        "import sys, core.agent_harness, core.agent_harness.spi; "
        "print('core.agent.agent' in sys.modules, "
        "'core.agent_harness.turns.action_driver' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"], out.stdout
