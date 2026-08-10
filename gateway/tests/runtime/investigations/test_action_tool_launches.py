"""Pins Defect A: the ``investigation_start`` action tool never launched anything.

``GatewayInvestigationLaunchPorts.run_foreground_investigation`` used to log a
warning and return ``COMPLETED`` without ever calling the ``run`` closure it was
handed, so ``launch_investigation`` recorded success and the model told the user
an investigation was underway when nothing had launched. Confirmed live in the
production gateway log, twice.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

from rich.console import Console

from core.agent_harness.tools.tool_context import ActionToolContext
from core.tool_framework.utils.call_identity import bound_tool_call_id
from gateway.core.chat import bound_delivery_target
from gateway.core.investigations.launch_ports import gateway_investigation_launch_ports
from gateway.core.investigations.launch_record import (
    DetachedLaunchRecord,
    bound_detached_launch_record,
)
from tools.interactive_shell.actions.investigation import execute_investigation_tool


class _FakeSession:
    """The minimal ``InvestigationSession`` surface the launch flow reads."""

    def __init__(self) -> None:
        self.accumulated_context: dict[str, Any] = {}
        self.records: list[tuple[str, str, bool]] = []

    def record(self, kind: str, value: str, *, ok: bool = True, **metadata: Any) -> None:
        _ = metadata
        self.records.append((kind, value, ok))


def _make_context(session: _FakeSession) -> ActionToolContext:
    return ActionToolContext(
        session=session,
        console=Console(file=StringIO(), force_terminal=False),
        investigation_ports=gateway_investigation_launch_ports(),
    )


def test_investigation_start_tool_queues_a_detached_run(
    delivery_target, notifier, register_notifier
):
    """A bound delivery target with a registered notifier must actually launch.

    Before the fix, this posted no ack and recorded a false success — the run
    closure (``launch_detached_investigation`` via ``run_text_investigation``)
    was never invoked.
    """
    register_notifier(notifier)
    session = _FakeSession()
    ctx = _make_context(session)

    with bound_delivery_target(delivery_target):
        handled = execute_investigation_tool({"alert_text": "checkout latency is up"}, ctx)

    assert handled is True
    assert len(notifier.acks) == 1
    assert session.records == [("alert", "checkout latency is up", True)]


def test_action_tool_launch_stamps_the_shared_launch_record(
    delivery_target, notifier, register_notifier
):
    """The action-tool path bypasses ``GatewayDetachedLauncher`` entirely.

    ``GatewayInvestigationLaunchPorts.run_text_investigation`` calls
    ``launch_detached_investigation`` directly — it never goes through
    ``GatewayDetachedLauncher.launch``, which only the slash/adapter path
    (``current_detached_launcher()``) uses. Stamping the launch record inside
    ``GatewayDetachedLauncher.launch`` instead of inside
    ``launch_detached_investigation`` itself would leave this path's launches
    unrecorded — the "leave this row open" signal for Slack's
    ``investigation_start`` tool would silently break while the slash path
    stayed fine, because nothing exercises this path through the launcher.
    """
    register_notifier(notifier)
    session = _FakeSession()
    ctx = _make_context(session)
    record = DetachedLaunchRecord()

    with (
        bound_delivery_target(delivery_target),
        bound_detached_launch_record(record),
        bound_tool_call_id("call-77"),
    ):
        handled = execute_investigation_tool({"alert_text": "checkout latency is up"}, ctx)

    assert handled is True
    assert record.any_accepted
    assert record.call_detached("call-77")


def test_refused_launch_records_failure(delivery_target):
    """A refusal (no notifier for the platform) must record ``ok=False``.

    Mapping ``"refused"`` to ``COMPLETED`` would make a launch nobody can
    deliver to look like a successful investigation.
    """
    session = _FakeSession()
    ctx = _make_context(session)

    with bound_delivery_target(delivery_target):
        handled = execute_investigation_tool({"alert_text": "checkout latency is up"}, ctx)

    assert handled is True
    assert session.records == [("alert", "checkout latency is up", False)]
