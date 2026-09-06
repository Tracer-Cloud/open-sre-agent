"""Python sandbox runner with timeout and restricted access."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Any

from config.constants import (
    OPENSRE_TMP_DIR,
    SANDBOX_BASE_ENV_KEYS,
    SANDBOXED_TEMP_ENV_KEYS,
    ensure_opensre_tmp_dir,
)

DEFAULT_TIMEOUT: int = 30
MAX_TIMEOUT: int = 60
_SANDBOX_TMP_ROOT = os.path.realpath(os.fspath(OPENSRE_TMP_DIR))
# An allowlist, not a passthrough: anything absent here is dropped. The second
# An allowlist, not a passthrough: anything absent here is dropped. The Windows
# entries are the keys the OS itself requires — without SYSTEMROOT, Winsock
# cannot initialise and every socket call fails with WinError 10106.
# SYSTEMDRIVE travels with it because
# Windows resolves the shell folders from a REG_EXPAND_SZ holding the literal
# "%SystemDrive%\\ProgramData", expanded against this environment. Drop the key
# and the token stays literal, leaving a path with no drive letter and no
# leading separator — i.e. a *relative* path, resolved against the child's cwd.
# Two things follow, and which one you see depends on the interpreter:
# python.org CPython fails the lookup silently and hands back an empty string,
# so generated code proceeds on a wrong path; MSIX/Store CPython additionally
# writes its packaging-layer cache to the relative path, creating an actual
# "%SystemDrive%" tree under the child's cwd — a write outside the sandbox root,
# made by the OS at process start rather than through the guarded open() below.
# TEMP, TMP, TMPDIR and USERPROFILE are listed but *not* forwarded: they are
# rewritten to the sandbox temp root in _sandbox_env, because the injected guard
# only intercepts builtins.open and tempfile's OS-level file creation bypasses
# it. Forwarding the host values would let ordinary mkstemp() calls land in host
# temp directories, outside the documented write-containment contract.
# Deliberately excluded: COMSPEC (the preamble blocks subprocess spawning) and
# APPDATA/LOCALAPPDATA (config homes, matching the omission of XDG_CONFIG_HOME).

# Preamble injected before user code when network access is disabled.
_NETWORK_BLOCK_PREAMBLE = textwrap.dedent("""\
    import socket as _socket_module

    class _BlockedSocket:
        def __init__(self, *args, **kwargs):
            raise PermissionError("Network access is not permitted in sandbox mode")

    _socket_module.socket = _BlockedSocket

    def _blocked_create_connection(*args, **kwargs):
        raise PermissionError("Network access is not permitted in sandbox mode")

    def _blocked_getaddrinfo(*args, **kwargs):
        raise PermissionError("Network access is not permitted in sandbox mode")

    _socket_module.create_connection = _blocked_create_connection
    _socket_module.getaddrinfo = _blocked_getaddrinfo
""")

# Preamble always injected before user code: restricts filesystem writes and subprocesses.
_SANDBOX_PREAMBLE = textwrap.dedent(f"""\
    import builtins as _builtins_module
    import os as _os_module

    _ALLOWED_WRITE_ROOTS = ({_SANDBOX_TMP_ROOT!r},)

    _original_open = _builtins_module.open

    def _restricted_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes)) or hasattr(file, "__fspath__"):
            mode_str = str(mode)
            if any(c in mode_str for c in ("w", "a", "x")):
                abs_path = _os_module.path.realpath(_os_module.fspath(file))
                if not any(
                    abs_path == root or abs_path.startswith(root + _os_module.sep)
                    for root in _ALLOWED_WRITE_ROOTS
                ):
                    raise PermissionError(
                        f"Write access denied outside the OpenSRE temp directory: {{file}}"
                    )
        return _original_open(file, mode, *args, **kwargs)

    _builtins_module.open = _restricted_open

    import subprocess as _subprocess_module
    import os as _os_shell_module

    def _blocked_subprocess(*args, **kwargs):
        raise PermissionError("Subprocess execution is not permitted in sandbox mode")

    _subprocess_module.Popen = _blocked_subprocess
    _subprocess_module.call = _blocked_subprocess
    _subprocess_module.check_call = _blocked_subprocess
    _subprocess_module.check_output = _blocked_subprocess
    _subprocess_module.run = _blocked_subprocess

    _os_shell_module.system = _blocked_subprocess
    _os_shell_module.popen = _blocked_subprocess

""")


@dataclass
class SandboxResult:
    """Result of a sandboxed Python execution."""

    code: str
    inputs: dict[str, Any]
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def run_python_sandbox(
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
    allow_network: bool = False,
) -> SandboxResult:
    """Run Python code in a sandboxed subprocess with timeout and access restrictions.

    Network access is blocked by replacing ``socket.socket`` and related helpers
    with a class that raises ``PermissionError``. Filesystem writes are restricted
    to the OpenSRE temp directory, so any attempt to open a file outside that
    directory for writing raises ``PermissionError``. Execution is capped at
    *timeout* seconds.

    Args:
        code: Python source code to execute.
        inputs: Optional mapping injected into the script's global scope as the
            ``inputs`` variable.
        timeout: Maximum wall-clock time in seconds.  Capped at
            :data:`MAX_TIMEOUT`.
        env: Optional approved environment variables to expose to the subprocess.
        allow_network: If True, do not inject the network-blocking preamble.

    Returns:
        :class:`SandboxResult` carrying captured stdout/stderr, exit code, and
        timeout/error metadata.
    """
    effective_timeout = min(max(1, timeout), MAX_TIMEOUT)
    effective_inputs: dict[str, Any] = inputs or {}

    inputs_injection = ""
    if effective_inputs:
        inputs_json = json.dumps(effective_inputs)
        inputs_injection = (
            f"import json as _json_module; inputs = _json_module.loads({inputs_json!r})\n"
        )

    network_preamble = "" if allow_network else _NETWORK_BLOCK_PREAMBLE
    full_code = network_preamble + _SANDBOX_PREAMBLE + inputs_injection + code

    tmp_path: str | None = None
    try:
        ensure_opensre_tmp_dir()
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            dir=OPENSRE_TMP_DIR,
        ) as tmp:
            tmp.write(full_code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=effective_timeout,
            env=_sandbox_env(env),
        )
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout="",
            stderr="",
            exit_code=-1,
            timed_out=True,
            error=f"Execution timed out after {effective_timeout} seconds",
        )
    except Exception as exc:
        return SandboxResult(
            code=code,
            inputs=effective_inputs,
            stdout="",
            stderr="",
            exit_code=-1,
            timed_out=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _sandbox_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Build a narrow subprocess environment plus explicitly approved values.

    Keys in SANDBOXED_TEMP_ENV_KEYS are not forwarded from the host:
    they are rewritten to the sandbox temp root so the child's own temp-file
    APIs (tempfile reads TEMP/TMP on Windows, TMPDIR on POSIX) cannot write
    outside OPENSRE_TMP_DIR, where the builtins.open guard gives no coverage
    (tempfile uses lower-level OS file creation).
    """
    sandbox_env: dict[str, str] = {}
    for key in SANDBOX_BASE_ENV_KEYS:
        if key in SANDBOXED_TEMP_ENV_KEYS:
            # Never forward the host value: point the child's temp locations at
            # the sandbox root instead, so writes stay inside the sandbox even
            # when they bypass the guarded open().
            sandbox_env[key] = _SANDBOX_TMP_ROOT
            continue
        value = os.environ.get(key)
        if value:
            sandbox_env[key] = value
    if extra_env:
        sandbox_env.update({key: str(value) for key, value in extra_env.items() if value})
    return sandbox_env
