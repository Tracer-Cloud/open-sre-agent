"""Python sandbox runner with timeout and restricted access.

**These guards are not a security boundary.** They are injected into the same
interpreter that runs the supplied code, so code that is *trying* to get out
can: ``importlib.reload`` restores any patched module, ``ctypes`` and
``os.posix_spawn`` were never patched, and a child interpreter started by any
route receives no preamble at all. Treat the restrictions as protection against
an agent damaging the host by accident, and gate the calling tool on approval
wherever the code author is not the operator.
"""

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

from config.constants import OPENSRE_TMP_DIR, ensure_opensre_tmp_dir

DEFAULT_TIMEOUT: int = 30
MAX_TIMEOUT: int = 60
_SANDBOX_TMP_ROOT = os.path.realpath(os.fspath(OPENSRE_TMP_DIR))
_BASE_ENV_KEYS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "TMPDIR",
)

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
    import io as _io_module
    import os as _os_module

    _ALLOWED_WRITE_ROOTS = ({_SANDBOX_TMP_ROOT!r},)

    def _write_allowed(path):
        abs_path = _os_module.path.realpath(_os_module.fspath(path))
        return any(
            abs_path == root or abs_path.startswith(root + _os_module.sep)
            for root in _ALLOWED_WRITE_ROOTS
        )

    _original_open = _builtins_module.open

    def _restricted_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes)) or hasattr(file, "__fspath__"):
            mode_str = str(mode)
            if any(c in mode_str for c in ("w", "a", "x")):
                if not _write_allowed(file):
                    raise PermissionError(
                        f"Write access denied outside the OpenSRE temp directory: {{file}}"
                    )
        return _original_open(file, mode, *args, **kwargs)

    _builtins_module.open = _restricted_open
    # pathlib.Path.open/write_text/write_bytes resolve io.open, which is a
    # separate binding from builtins.open. Patching only the latter left every
    # pathlib write unguarded.
    _io_module.open = _restricted_open

    _original_os_open = _os_module.open

    def _restricted_os_open(path, flags, *args, **kwargs):
        _write_flags = (
            _os_module.O_WRONLY | _os_module.O_RDWR | _os_module.O_APPEND | _os_module.O_CREAT
        )
        if flags & _write_flags and not _write_allowed(path):
            raise PermissionError(
                f"Write access denied outside the OpenSRE temp directory: {{path}}"
            )
        return _original_os_open(path, flags, *args, **kwargs)

    _os_module.open = _restricted_os_open

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
    """Run Python code in a subprocess with a timeout and best-effort restrictions.

    Network access is blocked by replacing ``socket.socket`` and related helpers
    with a class that raises ``PermissionError``. Filesystem writes are restricted
    to the OpenSRE temp directory via ``builtins.open``, ``io.open`` and
    ``os.open``. Execution is capped at *timeout* seconds.

    As the module docstring says, none of this contains hostile code — the guards
    live in the interpreter being guarded. Callers that accept code from anyone
    other than the operator must add their own gate (see
    ``PythonExecutionTool.requires_approval``).

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
            # Without this the script is written in the locale encoding (cp1252
            # on Windows) while the interpreter reads .py as UTF-8, so any
            # non-ASCII character in generated code dies with a SyntaxError.
            encoding="utf-8",
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
    """Build a narrow subprocess environment plus explicitly approved values."""
    # stdout/stderr are decoded as UTF-8 below, so the child must encode as
    # UTF-8 too. Without this it writes in its locale encoding (cp1252 on
    # Windows) and every non-ASCII character comes back as mojibake.
    sandbox_env: dict[str, str] = {"PYTHONIOENCODING": "utf-8"}
    for key in _BASE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            sandbox_env[key] = value
    if extra_env:
        sandbox_env.update({key: str(value) for key, value in extra_env.items() if value})
    return sandbox_env
