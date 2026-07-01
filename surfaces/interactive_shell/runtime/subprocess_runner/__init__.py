"""Execute planned opensre CLI actions.

Shell command execution lives in ``tools.interactive_shell.shell`` (parsing,
policy, ``execute_shell_command``, and the ``run_shell_command`` / ``run_cd`` /
``run_pwd`` runner); it is intentionally not re-exported here. Synthetic test
execution lives in ``tools.interactive_shell.synthetic`` (the
``run_synthetic_test`` / ``watch_synthetic_subprocess`` runner), Claude Code
implementation execution lives in ``tools.interactive_shell.implementation.claude_code_executor``
(``run_claude_code_implementation``), and sample-alert / free-text investigation
execution lives in ``tools.interactive_shell.actions.sample_alert`` /
``tools.interactive_shell.actions.investigation`` (``run_sample_alert`` /
``run_text_investigation``); none are re-exported here. All reuse the shared
subprocess-streaming primitives in ``task_streaming``.

Public API is stable: all names exported below are importable directly from
``subprocess_runner`` and will remain so regardless of internal submodule changes.

Stdlib modules ``os``, ``subprocess``, and ``threading`` are re-imported here so
that tests can patch them via the full ``subprocess_runner.<module>.<attr>`` path
(e.g. ``subprocess_runner.subprocess.Popen``). Since these are module singletons in
``sys.modules``, patching via this attribute also affects the actual call sites
inside the submodules.
"""

from __future__ import annotations

# Stdlib singletons — imported so that monkeypatch paths resolve correctly in tests:
# ``"…subprocess_runner.os.chdir"``, ``"…subprocess_runner.subprocess.Popen"``,
# ``"…subprocess_runner.threading.Thread"``, ``"…subprocess_runner.time.sleep"``,
# ``"…subprocess_runner.Path.cwd"``.
import os
import subprocess
import threading
import time
from pathlib import Path

from .background_task_executor import start_background_cli_task
from .opensre_cli_runner import (
    _INTERACTIVE_OPENSRE_COMMAND_PATHS,
    _OPENSRE_BLOCKED_SUBCOMMANDS,
    OpensreCommandClass,
    OpensreExecutionMode,
    OpensreExecutionPlan,
    OpensreRunOutcome,
    OpensreRunResult,
    _build_opensre_execution_plan,
    _classify_opensre_command,
    _is_interactive_wizard,
    _opensre_confirmation_reason,
    _run_opensre_foreground,
    _run_opensre_foreground_streaming,
    build_opensre_cli_argv,
    print_interactive_wizard_handoff,
    run_opensre_cli_command,
    run_opensre_cli_command_result,
)
from .task_streaming import (
    _MAX_COMMAND_OUTPUT_CHARS,
    _MIN_SUBPROCESS_TERMINAL_WIDTH,
    _SYNTHETIC_DIAG_CHARS,
    _SYNTHETIC_POLL_SECONDS,
    _TASK_OUTPUT_JOIN_TIMEOUT_SECONDS,
    _TASK_OUTPUT_PREFIX_WIDTH,
    CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS,
    SHELL_COMMAND_TIMEOUT_SECONDS,
    SYNTHETIC_TEST_TIMEOUT_SECONDS,
    _console_file_is_tty,
    _join_task_output_streams,
    _print_task_output_line,
    _pump_task_pty,
    _pump_task_stream,
    _should_use_pty,
    _start_task_output_streams,
    _subprocess_env_with_aligned_width,
    read_diag,
    read_task_output,
    terminate_child_process,
)

__all__ = [
    "CLAUDE_CODE_IMPLEMENTATION_TIMEOUT_SECONDS",
    "SHELL_COMMAND_TIMEOUT_SECONDS",
    "SYNTHETIC_TEST_TIMEOUT_SECONDS",
    "OpensreCommandClass",
    "OpensreExecutionMode",
    "OpensreExecutionPlan",
    "OpensreRunOutcome",
    "OpensreRunResult",
    "Path",
    "_INTERACTIVE_OPENSRE_COMMAND_PATHS",
    "_MAX_COMMAND_OUTPUT_CHARS",
    "_MIN_SUBPROCESS_TERMINAL_WIDTH",
    "_OPENSRE_BLOCKED_SUBCOMMANDS",
    "_SYNTHETIC_DIAG_CHARS",
    "_SYNTHETIC_POLL_SECONDS",
    "_TASK_OUTPUT_JOIN_TIMEOUT_SECONDS",
    "_TASK_OUTPUT_PREFIX_WIDTH",
    "_classify_opensre_command",
    "_build_opensre_execution_plan",
    "_console_file_is_tty",
    "_is_interactive_wizard",
    "_join_task_output_streams",
    "_opensre_confirmation_reason",
    "_print_task_output_line",
    "_pump_task_pty",
    "_pump_task_stream",
    "_run_opensre_foreground",
    "_run_opensre_foreground_streaming",
    "_should_use_pty",
    "_start_task_output_streams",
    "_subprocess_env_with_aligned_width",
    "build_opensre_cli_argv",
    "os",
    "print_interactive_wizard_handoff",
    "read_diag",
    "read_task_output",
    "run_opensre_cli_command",
    "run_opensre_cli_command_result",
    "start_background_cli_task",
    "subprocess",
    "terminate_child_process",
    "threading",
    "time",
]
