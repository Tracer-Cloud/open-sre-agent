"""Execute planned shell, sample alert, and synthetic test actions.

Public API is stable: all names exported below are importable directly from
``action_executor`` and will remain so regardless of internal submodule changes.

Stdlib modules ``os``, ``subprocess``, and ``threading`` are re-imported here so
that tests can patch them via the full ``action_executor.<module>.<attr>`` path
(e.g. ``action_executor.subprocess.Popen``). Since these are module singletons in
``sys.modules``, patching via this attribute also affects the actual call sites
inside the submodules.
"""

from __future__ import annotations

# Stdlib singletons — imported so that monkeypatch paths resolve correctly in tests:
# ``"…action_executor.os.chdir"``, ``"…action_executor.subprocess.Popen"``,
# ``"…action_executor.threading.Thread"``, ``"…action_executor.time.sleep"``,
# ``"…action_executor.Path.cwd"``.
import os
import subprocess
import threading
import time
from pathlib import Path

# execute_shell_command is imported here so that the monkeypatch path
# ``"…action_executor.execute_shell_command"`` resolves in tests. The
# actual call site in shell_runner.py uses ``_ae_resolve`` to pick up any patch.
from cli.interactive_shell.shell import execute_shell_command

# ClaudeCodeAdapter is imported here so that the monkeypatch path
# ``"…action_executor.ClaudeCodeAdapter"`` resolves in tests.
from integrations.llm_cli.claude_code import ClaudeCodeAdapter

from .background_tasks import start_background_cli_task
from .implementation_runner import run_claude_code_implementation
from .investigation_runner import run_sample_alert, run_text_investigation
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
    _should_run_opensre_in_foreground,
    print_interactive_wizard_handoff,
    run_opensre_cli_command,
    run_opensre_cli_command_result,
)
from .shell_runner import run_cd_command, run_pwd_command, run_shell_command
from .synthetic_tasks import (
    run_synthetic_test,
    watch_synthetic_subprocess,
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
    "ClaudeCodeAdapter",
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
    "_should_run_opensre_in_foreground",
    "_should_use_pty",
    "_start_task_output_streams",
    "_subprocess_env_with_aligned_width",
    "execute_shell_command",
    "os",
    "print_interactive_wizard_handoff",
    "read_diag",
    "read_task_output",
    "run_claude_code_implementation",
    "run_cd_command",
    "run_opensre_cli_command",
    "run_opensre_cli_command_result",
    "run_pwd_command",
    "run_sample_alert",
    "run_text_investigation",
    "run_shell_command",
    "run_synthetic_test",
    "start_background_cli_task",
    "subprocess",
    "terminate_child_process",
    "threading",
    "time",
    "watch_synthetic_subprocess",
]
