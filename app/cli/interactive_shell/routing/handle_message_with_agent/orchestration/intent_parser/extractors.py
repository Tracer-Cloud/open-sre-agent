"""Extractor functions that parse user text into planned actions."""

from __future__ import annotations

import re
import shlex
import shutil
from pathlib import Path

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    PromptClause,
)

from .action_builders import (
    implementation_action,
    investigation_action,
    llm_provider_action,
    shell_action,
    task_cancel_action,
)
from .patterns import (
    IMPLEMENTATION_RE,
    QUOTED_INVESTIGATION_RE,
    TASK_CANCEL_GENERIC_RE,
    TASK_CANCEL_GENERIC_TRIGGER_RE,
    TASK_CANCEL_ID_RE,
    TASK_CANCEL_SYNTHETIC_RE,
    TASK_CANCEL_TRIGGER_RE,
    IS_WINDOWS,
    _EXPLICIT_SHELL_RE,
    _LLM_PROVIDER_RE,
    _LLM_PROVIDER_SWITCH_RE,
    _NON_COMMAND_STARTS,
    _SHELL_BUILTINS,
    _SHELL_PROMPT_RE,
)


def mentioned_integration_services(text: str) -> list[str]:
    """Return configured integration service names mentioned in user text."""
    # Deferred to function scope: this module is loaded as a side-effect of
    # `app.integrations.registry` (via the `github_mcp` -> `interactive_shell`
    # back-edge), and `MANAGED_INTEGRATION_SERVICES` is resolved lazily from
    # the registry. A module-level import here triggers a recursive __getattr__
    # while the registry is still partially initialized. See #1973.
    from app.cli.support.constants import MANAGED_INTEGRATION_SERVICES

    lower = text.lower()
    services: list[str] = []
    for service in MANAGED_INTEGRATION_SERVICES:
        service_text = service.replace("_", " ")
        service_re = re.escape(service_text).replace(r"\ ", r"[\s_-]+")
        if re.search(rf"\b{service_re}\b", lower):
            services.append(service)
    return services


def strip_wrapping_quotes(command: str) -> str:
    stripped = command.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"`", "'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def normalize_shell_command(command: str) -> str | None:
    normalized = strip_wrapping_quotes(command)
    if not normalized or "\n" in normalized or "\r" in normalized:
        return None
    lower = normalized.lower()
    if lower.startswith(("a ", "an ")) or "investigation" in lower:
        return None
    return normalized


def first_command_token(command: str) -> str | None:
    try:
        tokens = shlex.split(command, posix=not IS_WINDOWS)
    except ValueError:
        # `shlex` in POSIX mode treats `\` as an escape character, which breaks
        # common Windows paths such as `cd C:\` (trailing backslash).
        try:
            tokens = shlex.split(command, posix=False)
        except ValueError:
            return None
    if not tokens:
        return None
    return tokens[0]


def looks_like_direct_shell_command(text: str) -> bool:
    first = first_command_token(text)
    if first is None:
        return False
    if first.lower() in _NON_COMMAND_STARTS:
        return False
    if first.lower() in _SHELL_BUILTINS:
        return True
    if first.startswith(("./", "../", "/")):
        return Path(first).exists()
    return shutil.which(first) is not None


def extract_shell_command(clause: PromptClause) -> PlannedAction | None:
    prompt_match = _SHELL_PROMPT_RE.match(clause.text)
    if prompt_match is not None:
        command = normalize_shell_command(prompt_match.group("command"))
        return (
            shell_action(command, clause.position + prompt_match.start("command"))
            if command
            else None
        )

    explicit_match = _EXPLICIT_SHELL_RE.match(clause.text)
    if explicit_match is not None:
        command = normalize_shell_command(explicit_match.group("command"))
        if command is None:
            return None
        return shell_action(command, clause.position + explicit_match.start("command"))

    command = normalize_shell_command(clause.text)
    if command is not None and command.startswith("!") and len(command) > 1:
        return shell_action(command, clause.position)
    if command is not None and looks_like_direct_shell_command(command):
        return shell_action(command, clause.position)
    return None


def extract_task_cancel_request(clause: PromptClause) -> PlannedAction | None:
    trigger = TASK_CANCEL_TRIGGER_RE.search(clause.text)
    if trigger is None:
        return None

    task_id = TASK_CANCEL_ID_RE.search(clause.text)
    if task_id is not None:
        return task_cancel_action(
            task_id.group("task_id").lower(), clause.position + task_id.start()
        )

    synthetic = TASK_CANCEL_SYNTHETIC_RE.search(clause.text)
    if synthetic is not None:
        return task_cancel_action("synthetic_test", clause.position + synthetic.start())

    generic_trigger = TASK_CANCEL_GENERIC_TRIGGER_RE.search(clause.text)
    generic = TASK_CANCEL_GENERIC_RE.search(clause.text)
    if generic_trigger is not None and generic is not None:
        return task_cancel_action("task", clause.position + generic.start())

    return None


def extract_implementation_request(clause: PromptClause) -> PlannedAction | None:
    match = IMPLEMENTATION_RE.match(clause.text)
    if match is None:
        return None
    request = (match.group("request") or "").strip()
    content = request or clause.text.strip()
    position = clause.position + match.start("trigger")
    return implementation_action(content, position)


def extract_quoted_investigation_request(clause: PromptClause) -> PlannedAction | None:
    match = QUOTED_INVESTIGATION_RE.search(clause.text)
    if match is None:
        return None
    payload = (
        match.group("double") or match.group("single") or match.group("backtick") or ""
    ).strip()
    if not payload:
        return None
    group_name = (
        "double" if match.group("double") else "single" if match.group("single") else "backtick"
    )
    return investigation_action(payload, clause.position + match.start(group_name))


def extract_quoted_investigation_request_text(text: str) -> PlannedAction | None:
    match = QUOTED_INVESTIGATION_RE.search(text)
    if match is None:
        return None
    payload = (
        match.group("double") or match.group("single") or match.group("backtick") or ""
    ).strip()
    if not payload:
        return None
    group_name = (
        "double" if match.group("double") else "single" if match.group("single") else "backtick"
    )
    return investigation_action(payload, match.start(group_name))


def extract_llm_provider_switch(clause: PromptClause) -> PlannedAction | None:
    if _LLM_PROVIDER_SWITCH_RE.search(clause.text) is None:
        return None

    provider_matches = list(_LLM_PROVIDER_RE.finditer(clause.text))
    if not provider_matches:
        return None

    target = provider_matches[-1]
    provider = target.group("provider").lower()
    return llm_provider_action(provider, clause.position + target.start("provider"))
