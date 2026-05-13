"""End-to-end pytest entrypoint for the OpenClaw integration suite.

Skips cleanly when the ``openclaw`` CLI is not on ``$PATH`` so the
default contributor flow (``make test-cov``) and CI shards that don't
gate on ``ci:openclaw`` stay green.

Each fault scenario lands in its own ``test_<scenario>.py`` next to this
file (issues #3, #5, #6) so the scenarios are independently mergeable.
This file holds the cross-scenario smoke test that proves the suite is
wired up correctly.
"""

from __future__ import annotations

import pytest

from tests.e2e.openclaw.infrastructure_sdk.local import (
    OPENCLAW_CLI_SKIP_REASON,
    OpenClawHandle,
    boot_openclaw,
    openclaw_cli_available,
    teardown_openclaw,
)

# Marker is also applied via ``pytestmark`` so the whole module is
# excluded from ``make test-cov`` (which runs ``-m "not synthetic"`` and
# we explicitly exclude e2e dirs there too — both paths skip this).
pytestmark = pytest.mark.e2e


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
def test_openclaw_e2e_suite_scaffold_smoke() -> None:
    """Smoke test that the e2e package imports without error.

    Confirms the scaffold is in place and the import graph is wired so
    subsequent scenario PRs (#issue-3 gateway-down, #issue-5
    tool-call-timeout, #issue-6 wrong-endpoint) can land without re-doing
    this plumbing.

    Skipped when the ``openclaw`` CLI is absent so contributor laptops
    that haven't installed it can still run the full unit suite.
    """
    from tests.e2e.openclaw import orchestrator, use_case
    from tests.e2e.openclaw.infrastructure_sdk import fault_injection, local

    assert hasattr(local, "boot_openclaw")
    assert hasattr(local, "teardown_openclaw")
    assert hasattr(local, "OpenClawHandle")
    assert hasattr(fault_injection, "inject_gateway_down")
    assert hasattr(fault_injection, "inject_sleeping_tool_call")
    assert hasattr(fault_injection, "inject_wrong_endpoint")
    assert hasattr(use_case, "drive_openclaw_conversation")
    assert hasattr(orchestrator, "run_openclaw_investigation")


def test_boot_openclaw_skips_when_with_gateway_false() -> None:
    """``with_gateway=False`` returns a bare handle without spawning
    anything. Useful for the gateway-down fault scenario, where the
    test wants to exercise the "no Gateway running" path.

    This sub-test does NOT require the openclaw CLI to be installed
    because no process is spawned — only the early skip-checks run.
    """
    if not openclaw_cli_available():
        pytest.skip(OPENCLAW_CLI_SKIP_REASON)
    handle = boot_openclaw(with_gateway=False)
    assert isinstance(handle, OpenClawHandle)
    assert handle.gateway_pid is None
    assert handle.gateway_url is None
    # Teardown of a bare handle must be a no-op.
    teardown_openclaw(handle)


@pytest.mark.skipif(not openclaw_cli_available(), reason=OPENCLAW_CLI_SKIP_REASON)
def test_boot_openclaw_starts_gateway_and_teardown_kills_it() -> None:
    """End-to-end boot + healthcheck + teardown smoke.

    Spawns the dev Gateway on the isolated dev port (19001), waits for
    the WebSocket health endpoint to answer, asserts the handle is
    populated correctly, then tears it down and verifies the process is
    gone. ``--dev`` keeps state under ``~/.openclaw-dev`` so the user's
    real OpenClaw setup is untouched.
    """
    import os

    handle = boot_openclaw()
    try:
        assert handle.gateway_pid is not None
        assert handle.gateway_url is not None and handle.gateway_url.startswith("ws://127.0.0.1:")
        assert handle.gateway_port is not None and handle.gateway_port > 0
        assert handle.log_path is not None and handle.log_path.exists()
        # Process is alive at this point.
        os.kill(handle.gateway_pid, 0)
    finally:
        teardown_openclaw(handle)

    # After teardown, the pid should be gone (ESRCH on os.kill(pid, 0)).
    if handle.gateway_pid is not None:
        with pytest.raises(ProcessLookupError):
            os.kill(handle.gateway_pid, 0)
