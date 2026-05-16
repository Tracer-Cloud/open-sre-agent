"""Boot and tear down a local OpenClaw instance for end-to-end tests.

Implements Unit A of #1484: spawn ``openclaw gateway run`` in dev mode
on an isolated port + workspace, poll the WebSocket health endpoint
until ready, return a populated :class:`OpenClawHandle`. Teardown sends
SIGTERM, waits 5s, escalates to SIGKILL if needed.

The MCP bridge (``openclaw mcp serve``) is **not** spawned here — it's a
stdio-only MCP server owned by whichever ``mcp.client.stdio.stdio_client``
context spawns it (see ``app.integrations.openclaw._open_openclaw_session``).
Fault injectors and use-case drivers spawn it themselves per-call.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ``--dev`` profile defaults — keeps tests isolated from a user's real
# OpenClaw setup. ``openclaw gateway run --dev`` writes state under
# ``~/.openclaw-dev`` and binds the WebSocket Gateway on port 19001.
_DEV_GATEWAY_PORT = 19001
_DEV_STATE_DIR = Path.home() / ".openclaw-dev"

# Healthcheck polling: tail the Gateway's own log for the deterministic
# ``[gateway] ready`` line that the daemon prints after binding its
# listeners. Boot is typically <2s; 20s gives plenty of headroom for
# cold-start on first run after ``--dev`` workspace creation.
#
# Why not ``openclaw gateway health``: the subcommand reads the user's
# real ``~/.openclaw/openclaw.json`` config to pick a target URL, so it
# probes the wrong port (18789 default) when we're on the dev port
# 19001. Passing ``--url`` is rejected without ``--token``/``--password``
# even when the Gateway runs with ``--auth none``. Log-tail is simpler,
# deterministic, and doesn't depend on auth setup or config state.
_HEALTHCHECK_POLL_INTERVAL_S = 0.1
_HEALTHCHECK_TIMEOUT_S = 20.0
_GATEWAY_READY_MARKER = b"[gateway] ready"
# OpenClaw emits ANSI color codes when it detects a TTY-ish environment
# (it does even when stdout is redirected to a file on darwin), which
# splits the ``[gateway] ready`` literal into ``[gateway]\x1b[…]m
# ready``. Strip ANSI escapes before searching so the marker matches
# either colored or uncolored output (CI runners are uncolored).
_ANSI_ESCAPE_RE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")

# SIGTERM grace before escalating to SIGKILL — matches the pattern used
# by ``app.cli.interactive_shell.orchestration.action_executor.terminate_child_process``.
_TEARDOWN_GRACE_S = 5.0


@dataclass
class OpenClawHandle:
    """Live handle to a booted local OpenClaw Gateway.

    Returned by :func:`boot_openclaw` and consumed by every fault
    injector. ``gateway_pid`` and ``gateway_url`` are populated only when
    the handle was booted with ``with_gateway=True``.
    """

    gateway_pid: int | None = None
    gateway_port: int | None = None
    gateway_url: str | None = None
    state_dir: Path | None = None
    log_path: Path | None = None
    _process: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    extra: dict[str, object] = field(default_factory=dict)


def openclaw_cli_available() -> bool:
    """True when the ``openclaw`` CLI is on ``$PATH``.

    Public helper so scenario test files can ``skipif`` on a single
    canonical check instead of redefining the function locally.
    """
    return shutil.which("openclaw") is not None


# Shared skip-reason strings + LLM credential probe used by every
# scenario test file. Centralized so the wording stays consistent and a
# new scenario doesn't have to copy/paste them.
OPENCLAW_CLI_SKIP_REASON = "openclaw CLI not installed — see tests/e2e/openclaw/README.md"
LLM_CREDENTIAL_SKIP_REASON = (
    "No LLM credential set (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY) "
    "— full RCA invocation skipped."
)
_LLM_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)


def llm_credentials_present() -> bool:
    """True when at least one OpenSRE-supported LLM API key is set.

    Full-RCA sub-tests skipif on this; use-case sub-tests run without
    an LLM key.
    """
    return any(os.environ.get(var) for var in _LLM_CREDENTIAL_ENV_VARS)


def _node_version_ok() -> bool:
    """OpenClaw needs Node 22.12+; older Node prints the requirement to
    stderr and exits non-zero, so we can detect by trying ``--version``.
    """
    if not openclaw_cli_available():
        return False
    try:
        result = subprocess.run(
            ["openclaw", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    combined = (result.stdout + result.stderr).lower()
    if "node.js" in combined and "required" in combined:
        return False
    return result.returncode == 0


def _skip_if_openclaw_unavailable() -> None:
    """Pytest-skip with a clear reason when OpenClaw can't run locally.

    Two failure modes:
    - CLI not installed → ``shutil.which`` returns None
    - CLI installed but Node version too old → ``openclaw --version``
      exits non-zero with the Node-version error
    """
    if not openclaw_cli_available():
        pytest.skip("openclaw CLI not installed — see tests/e2e/openclaw/README.md")
    if not _node_version_ok():
        pytest.skip(
            "openclaw requires Node 22.12+ — run `nvm install 22 && nvm use 22` "
            "and rerun (see tests/e2e/openclaw/README.md)"
        )


def _gateway_log_contains_ready(log_path: Path) -> bool:
    """True when the Gateway log contains the ``[gateway] ready`` marker."""
    try:
        with log_path.open("rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    return _GATEWAY_READY_MARKER in _ANSI_ESCAPE_RE.sub(b"", raw)


def _wait_for_healthy(
    process: subprocess.Popen[bytes], log_path: Path, port: int, deadline: float
) -> None:
    """Poll the Gateway log until it prints the ready marker.

    Bails early if the process dies before getting ready — re-raises a
    TimeoutError with a hint pointing at the captured log so the user
    can see the actual gateway error rather than waiting 20s.
    """
    while time.monotonic() < deadline:
        if _gateway_log_contains_ready(log_path):
            return
        if process.poll() is not None:
            raise TimeoutError(
                f"OpenClaw Gateway exited with code {process.returncode} before "
                f"becoming healthy. See {log_path} for the captured output."
            )
        time.sleep(_HEALTHCHECK_POLL_INTERVAL_S)
    raise TimeoutError(
        f"OpenClaw Gateway on port {port} did not become healthy within "
        f"{_HEALTHCHECK_TIMEOUT_S:.0f}s. See {log_path} for the captured output."
    )


def boot_openclaw(
    *,
    with_gateway: bool = True,
    port: int = _DEV_GATEWAY_PORT,
    log_dir: Path | None = None,
) -> OpenClawHandle:
    """Spawn ``openclaw gateway run --dev --bind loopback --auth none`` and
    block until the Gateway WebSocket healthcheck answers.

    Skips the calling test cleanly via :func:`pytest.skip` when the
    ``openclaw`` CLI is missing or running on too-old Node — keeps the
    suite green on contributor machines that haven't installed it.

    Always uses ``--dev`` so test state lives under ``~/.openclaw-dev``
    and never touches a user's real ``~/.openclaw`` directory.
    ``--bind loopback`` keeps the Gateway off the LAN. ``--auth none``
    skips token/password setup for test simplicity. ``--force`` cleans
    up any previous-test orphans on the same port.

    With ``with_gateway=False`` no process is spawned — the handle is a
    bare envelope used by fault scenarios that need to assert behavior
    when the Gateway is unreachable.
    """
    _skip_if_openclaw_unavailable()

    handle = OpenClawHandle(state_dir=_DEV_STATE_DIR)
    if not with_gateway:
        return handle

    log_dir = log_dir or Path(os.environ.get("TMPDIR", "/tmp")) / "openclaw-e2e-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gateway-{port}-{int(time.time())}.log"
    handle.log_path = log_path
    handle.gateway_port = port
    handle.gateway_url = f"ws://127.0.0.1:{port}"

    # ``--allow-unconfigured`` is needed because a contributor's real
    # ``~/.openclaw/`` config can have an incomplete ``gateway.mode``
    # entry that blocks Gateway start with "existing config is missing
    # gateway.mode". The flag bypasses that check; ``--dev`` still
    # isolates runtime state under ``~/.openclaw-dev``.
    log_file = log_path.open("wb")
    try:
        process = subprocess.Popen(
            [
                "openclaw",
                "gateway",
                "run",
                "--dev",
                "--allow-unconfigured",
                "--bind",
                "loopback",
                "--auth",
                "none",
                "--force",
                "--port",
                str(port),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        log_file.close()
        raise

    handle._process = process
    handle.gateway_pid = process.pid

    try:
        _wait_for_healthy(
            process,
            log_path,
            port,
            deadline=time.monotonic() + _HEALTHCHECK_TIMEOUT_S,
        )
    except BaseException:
        # If healthcheck times out (or the user Ctrl+C's mid-boot), the
        # Gateway process is still alive — tear it down so we don't
        # leak it into the next test or back to the user's shell.
        teardown_openclaw(handle)
        raise
    return handle


def teardown_openclaw(handle: OpenClawHandle) -> None:
    """Tear down a previously booted OpenClaw Gateway.

    SIGTERM → wait :data:`_TEARDOWN_GRACE_S` → SIGKILL. Idempotent and
    safe to call on partial-boot handles (e.g. when healthcheck timed
    out leaving the Gateway alive but never ready).
    """
    process = handle._process
    if process is None or process.poll() is not None:
        return
    with contextlib.suppress(OSError):
        # ``start_new_session=True`` put the gateway in its own process
        # group — kill the group so any child processes (the gateway
        # spawns its own internal services) go down with it.
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=_TEARDOWN_GRACE_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_TEARDOWN_GRACE_S)
