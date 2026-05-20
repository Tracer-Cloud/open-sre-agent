"""Deterministic intent helpers used by the fallback action planner.

LLM-first planning is the primary path for non-slash requests. These helpers are
kept for deterministic fallback behavior and regression tests.

Public API is stable: all names exported below are importable directly from
``intent_parser`` and will remain so regardless of internal submodule changes.
"""

from __future__ import annotations

# ``shutil`` is imported here so that tests can import it via
# ``from intent_parser import shutil`` and monkeypatch ``shutil.which``
# against the module singleton.
import shutil  # noqa: F401

from .action_builders import (
    cli_command_action,
    implementation_action,
    investigation_action,
    llm_provider_action,
    sample_alert_action,
    shell_action,
    slash_action,
    synthetic_test_action,
    task_cancel_action,
)
from .clause_split import split_prompt_clauses
from .extractors import (
    extract_implementation_request,
    extract_llm_provider_switch,
    extract_quoted_investigation_request,
    extract_quoted_investigation_request_text,
    extract_shell_command,
    extract_task_cancel_request,
    looks_like_direct_shell_command,
    mentioned_integration_services,
    normalize_shell_command,
)
from .patterns import (
    ACTION_PATTERNS,
    CLAUSE_SPLIT_RE,
    IMPLEMENTATION_RE,
    INTEGRATION_CAPABILITY_RE,
    INTEGRATION_CONFIG_DETAIL_RE,
    INTEGRATION_DETAIL_RE,
    IS_WINDOWS,
    QUOTED_INVESTIGATION_RE,
    SAMPLE_ALERT_RE,
    SYNTHETIC_RDS_TEST_RE,
    TASK_CANCEL_GENERIC_RE,
    TASK_CANCEL_GENERIC_TRIGGER_RE,
    TASK_CANCEL_ID_RE,
    TASK_CANCEL_SYNTHETIC_RE,
    TASK_CANCEL_TRIGGER_RE,
    _EXPLICIT_SHELL_RE,
    _LLM_PROVIDER_NAMES,
    _LLM_PROVIDER_RE,
    _LLM_PROVIDER_SWITCH_RE,
    _NON_COMMAND_STARTS,
    _SHELL_BUILTINS,
    _SHELL_PROMPT_RE,
)
from .typo_normalization import (
    is_single_edit_typo,
    normalize_intent_text,
)

__all__ = [
    "ACTION_PATTERNS",
    "IMPLEMENTATION_RE",
    "INTEGRATION_CAPABILITY_RE",
    "INTEGRATION_CONFIG_DETAIL_RE",
    "INTEGRATION_DETAIL_RE",
    "QUOTED_INVESTIGATION_RE",
    "SAMPLE_ALERT_RE",
    "SYNTHETIC_RDS_TEST_RE",
    "cli_command_action",
    "extract_quoted_investigation_request",
    "extract_quoted_investigation_request_text",
    "extract_implementation_request",
    "extract_llm_provider_switch",
    "extract_shell_command",
    "extract_task_cancel_request",
    "investigation_action",
    "implementation_action",
    "is_single_edit_typo",
    "llm_provider_action",
    "looks_like_direct_shell_command",
    "mentioned_integration_services",
    "sample_alert_action",
    "slash_action",
    "shell_action",
    "normalize_intent_text",
    "split_prompt_clauses",
    "synthetic_test_action",
    "task_cancel_action",
]
