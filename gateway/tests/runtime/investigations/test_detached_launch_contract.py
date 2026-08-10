"""Structural invariants the detached-launch design depends on but cannot enforce.

``current_detached_launch_record()`` reads a contextvar bound around the whole
tool-call batch on the turn thread. That only works because the runtime forces
launch-capable tools onto that same thread — ``core/execution.py`` runs
parallel-safe calls on a thread pool via ``pool.submit``, which copies no
context. A launch tool that ever became parallel-safe would silently stop
seeing the bound launch record (and the bound delivery target), reverting to
whatever the pool thread's empty context resolves to.
"""

from __future__ import annotations

from tools.interactive_shell.actions.investigation import investigation_start_tool
from tools.interactive_shell.actions.sample_alert import alert_sample_tool
from tools.interactive_shell.actions.slash import slash_invoke_tool


def test_investigation_tools_are_not_parallel_safe() -> None:
    """A parallel-safe launch tool would silently lose its bound context.

    ``pool.submit`` (``core/execution.py``) copies no context for parallel-safe
    calls, so a launch tool run there would not see the delivery target or the
    turn's ``DetachedLaunchRecord`` — the launch would either refuse silently or
    (worse) leave the turn's ✅/👀 outcome un-attributed to the run it started.

    ``slash_invoke`` is included because ``/investigate`` reaches the same
    detached-launch path through it (``run_investigation_for_session`` /
    ``run_sample_alert_for_session``), and it is also the tool named in
    ``DETACHED_LAUNCH_TOOL_NAMES`` alongside the other two.
    """
    assert investigation_start_tool.parallel_safe is False
    assert alert_sample_tool.parallel_safe is False
    assert slash_invoke_tool.parallel_safe is False
