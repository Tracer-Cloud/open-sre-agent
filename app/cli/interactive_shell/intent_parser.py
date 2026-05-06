"""Parse natural-language terminal input into structured intents (pure helpers)."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path

from app.cli.interactive_shell.interaction_models import PlannedAction, PromptClause

ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:check|verify|show|get|run)\b.{0,80}?\b(?:health|status)\b"
            r"|"
            r"\bopensre\s+health\b",
            re.IGNORECASE,
        ),
        "/health",
    ),
    (
        re.compile(
            r"\b(?:show|list|get|which|what)\b.{0,80}?"
            r"\b(?:connected\s+)?(?:services|integrations)\b",
            re.IGNORECASE,
        ),
        "/list integrations",
    ),
    (
        re.compile(
            r"\b(?:show|tell\s+me|get|what(?:'s|\s+is)?|current)\b.{0,80}?"
            r"\b(?:cli\s+)?version\b"
            r"|"
            r"\bopensre\s+version\b",
            re.IGNORECASE,
        ),
        "/version",
    ),
)

SAMPLE_ALERT_RE = re.compile(
    r"\b(?:try|run|start|launch|fire|send|trigger)\b.{0,60}?"
    r"\b(?:sample|simple|test|demo)\s+(?:alert|event)\b",
    re.IGNORECASE,
)
SYNTHETIC_RDS_TEST_RE = re.compile(
    r"\b(?:run|start|launch|execute)\b.{0,80}?"
    r"\b(?:synthetic|benchmark|test)\b.{0,80}?"
    r"\b(?:r\s*d\s*s|postgres(?:ql)?|database|db)\b",
    re.IGNORECASE | re.DOTALL,
)
_LLM_PROVIDER_NAMES = frozenset(
    {
        "anthropic",
        "openai",
        "openrouter",
        "requesty",
        "gemini",
        "nvidia",
        "ollama",
        "codex",
        "claude-code",
        "gemini-cli",
    }
)
_LLM_PROVIDER_RE = re.compile(
    rf"\b(?P<provider>{'|'.join(sorted(_LLM_PROVIDER_NAMES, key=len, reverse=True))})\b",
    re.IGNORECASE,
)
_LLM_PROVIDER_SWITCH_RE = re.compile(
    r"\b(?:switch|change|set|use|select)\b.{0,120}?\b(?:llm|model|provider)\b"
    r"|"
    r"\b(?:switch|change|use|select)\s+(?:to|over\s+to)\b",
    re.IGNORECASE | re.DOTALL,
)

INTEGRATION_DETAIL_RE = re.compile(
    r"\b(tell\s+me|show|list|get|what)\b.{0,120}?"
    r"\b(integrations?|services?|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

INTEGRATION_CAPABILITY_RE = re.compile(
    r"\b(what\b.{0,60}\bcan\s+do|can\s+do|does|about)\b",
    re.IGNORECASE,
)

INTEGRATION_CONFIG_DETAIL_RE = re.compile(
    r"\b(show|list|get|connections?|connected|configured|credentials?)\b",
    re.IGNORECASE,
)

CLAUSE_SPLIT_RE = re.compile(r"\s+\b(?:and(?:\s+then)?|then)\b\s+", re.IGNORECASE)
_EXPLICIT_SHELL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute|exec)\s+"
    r"(?:this\s+)?(?:the\s+)?(?:shell\s+)?(?:command\s+)?(?::\s*)?(?P<command>.+?)\s*$",
    re.IGNORECASE,
)
_SHELL_PROMPT_RE = re.compile(r"^\s*\$\s+(?P<command>.+?)\s*$")
_NON_COMMAND_STARTS = frozenset(
    {
        "can",
        "could",
        "explain",
        "hello",
        "hey",
        "hi",
        "how",
        "please",
        "show",
        "tell",
        "thanks",
        "thank",
        "what",
        "when",
        "where",
        "which",
        "why",
    }
)
# Shell builtins that may not be discoverable via `shutil.which()` on all platforms.
# Keep this list intentionally small and add tests when extending it.
_SHELL_BUILTINS = frozenset({"cd", "pwd"})
IS_WINDOWS = os.name == "nt"


def slash_action(command: str, position: int) -> PlannedAction:
    return PlannedAction(kind="slash", content=command, position=position)


def shell_action(command: str, position: int) -> PlannedAction:
    return PlannedAction(kind="shell", content=command, position=position)


def sample_alert_action(template_name: str, position: int) -> PlannedAction:
    return PlannedAction(kind="sample_alert", content=template_name, position=position)


def synthetic_test_action(suite_name: str, position: int) -> PlannedAction:
    return PlannedAction(kind="synthetic_test", content=suite_name, position=position)


def llm_provider_action(provider: str, position: int) -> PlannedAction:
    return PlannedAction(kind="llm_provider", content=provider, position=position)


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


def split_prompt_clauses(message: str) -> list[PromptClause]:
    """Split compound prompts while preserving each clause's source position."""
    clauses: list[PromptClause] = []
    start = 0
    for match in CLAUSE_SPLIT_RE.finditer(message):
        raw = message[start : match.start()]
        stripped = raw.strip()
        if stripped:
            clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))
        start = match.end()

    raw = message[start:]
    stripped = raw.strip()
    if stripped:
        clauses.append(PromptClause(text=stripped, position=start + raw.index(stripped)))

    return clauses or [PromptClause(text=message.strip(), position=0)]


def extract_llm_provider_switch(clause: PromptClause) -> PlannedAction | None:
    if _LLM_PROVIDER_SWITCH_RE.search(clause.text) is None:
        return None

    provider_matches = list(_LLM_PROVIDER_RE.finditer(clause.text))
    if not provider_matches:
        return None

    target = provider_matches[-1]
    provider = target.group("provider").lower()
    return llm_provider_action(provider, clause.position + target.start("provider"))


__all__ = [
    "ACTION_PATTERNS",
    "INTEGRATION_CAPABILITY_RE",
    "INTEGRATION_CONFIG_DETAIL_RE",
    "INTEGRATION_DETAIL_RE",
    "IS_WINDOWS",
    "SAMPLE_ALERT_RE",
    "SYNTHETIC_RDS_TEST_RE",
    "extract_llm_provider_switch",
    "extract_shell_command",
    "looks_like_direct_shell_command",
    "sample_alert_action",
    "slash_action",
    "shell_action",
    "split_prompt_clauses",
    "synthetic_test_action",
]
