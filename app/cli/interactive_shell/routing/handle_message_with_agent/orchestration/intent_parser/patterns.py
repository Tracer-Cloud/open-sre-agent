"""Compiled regex patterns and static constants for intent matching."""

from __future__ import annotations

import os
import re

IS_WINDOWS = os.name == "nt"

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
            r"\b(?:show|list|get)\b.{0,80}?\b(?:services|integrations)\b"
            r"|"
            r"\b(?:which|what)\b.{0,80}?\b(?:connected|configured|local)\b.{0,40}?"
            r"\b(?:services|integrations)\b"
            r"|"
            r"\b(?:which|what)\b.{0,80}?\b(?:services|integrations)\b.{0,40}?"
            r"\b(?:connected|configured|local)\b",
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
    (
        re.compile(
            r"\b(?:run|start|open|launch)\b.{0,80}?\b(?:onboard(?:ing)?|setup|wizard)\b",
            re.IGNORECASE,
        ),
        "/onboard",
    ),
    (
        re.compile(
            r"(?<!how to )(?<!how do i )(?<!how can i )\b(?:deploy|ship|push)\b.{0,80}?"
            r"\b(?:to|opensre)\b"
            r"|"
            r"\bconnect\b.{0,80}?\b(?:opensre|ec2|nitro|instance|remote)\b",
            re.IGNORECASE,
        ),
        "/remote",
    ),
    (
        re.compile(
            r"\b(?:check|trigger|run|show|list|get|which|what)\b.{0,80}?"
            r"\b(?:remote(?:'s)?|deployed|deployments?)\b",
            re.IGNORECASE,
        ),
        "/remote",
    ),
    (
        re.compile(
            r"\b(?:run|list|browse|show|check)\b.{0,80}?\btests\b",
            re.IGNORECASE,
        ),
        "/tests",
    ),
    (
        re.compile(
            r"\b(?:audit|manage|show|list|test)\b.{0,80}?\bguardrails?\b",
            re.IGNORECASE,
        ),
        "/guardrails",
    ),
    (
        re.compile(
            r"\b(?:update|upgrade|check\s+for\s+new)\b.{0,80}?\b(?:version|opensre)\b",
            re.IGNORECASE,
        ),
        "/update",
    ),
    (
        re.compile(
            r"\b(?:uninstall|remove|delete|wipe)\b.{0,80}?\bopensre\b",
            re.IGNORECASE,
        ),
        "/uninstall",
    ),
    (
        re.compile(
            r"\b(?:list|show|manage|forget|register)\b.{0,80}?\bagents?\b",
            re.IGNORECASE,
        ),
        "/agents",
    ),
    (
        re.compile(
            r"\b(?:doctor|check\s+setup|diagnose|diagnostic)\b",
            re.IGNORECASE,
        ),
        "/doctor",
    ),
    (
        re.compile(
            # Only match bare "opensre <subcmd>" when opensre is at the very start of the
            # clause (i.e. the user typed it as a direct command). Matching mid-sentence
            # would fire on product-name references like "OpenSRE that I have running…"
            # and treat the next English word as a subcommand.
            r"(?:^|\A)\s*opensre\s+(?P<subcmd>(?!health|version)[a-z][a-z0-9-]*)(?:\s+(?P<rest>.*))?"
            r"|"
            r"\b(?:run|execute|use|try)\s+opensre\s+(?P<subcmd2>[a-z][a-z0-9-]*)(?:\s+(?P<rest2>.*))?\b",
            re.IGNORECASE,
        ),
        "cli_command",
    ),
)

SAMPLE_ALERT_RE = re.compile(
    r"\b(?:try|run|start|launch|fire|send|trigger)\b.{0,60}?"
    r"\b(?:sample|simple|test|demo)\s+(?:alert|event)\b",
    re.IGNORECASE,
)
QUOTED_INVESTIGATION_RE = re.compile(
    r"\b(?:send|start|run|launch|trigger)\b.{0,80}?\binvestigation\b.{0,260}?"
    r"(?:\"(?P<double>[^\"]+)\"|'(?P<single>[^']+)'|`(?P<backtick>[^`]+)`)",
    re.IGNORECASE | re.DOTALL,
)
SYNTHETIC_RDS_TEST_RE = re.compile(
    r"\b(?:run|start|launch|execute)\b.{0,80}?"
    r"\b(?:synthetic(?:\s+test)?|benchmark)\b"
    r"(?:.{0,80}?\b(?:r\s*d\s*s|postgres(?:ql)?|database|db)\b)?",
    re.IGNORECASE | re.DOTALL,
)
TASK_CANCEL_TRIGGER_RE = re.compile(r"\b(?:abort|cancel|kill|stop|terminate)\b", re.IGNORECASE)
TASK_CANCEL_ID_RE = re.compile(r"\b(?P<task_id>[0-9a-f]{4,16})\b", re.IGNORECASE)
TASK_CANCEL_SYNTHETIC_RE = re.compile(
    r"\b(?:synthetic|syntehtic)(?:[_\s-]?tests?)?\b|\bbenchmark\b",
    re.IGNORECASE,
)
TASK_CANCEL_GENERIC_TRIGGER_RE = re.compile(r"\b(?:abort|cancel)\b", re.IGNORECASE)
TASK_CANCEL_GENERIC_RE = re.compile(r"\b(?:job|process|run|task|work)\b", re.IGNORECASE)
IMPLEMENTATION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+)?"
    r"(?:(?:use|launch|run)\s+claude(?:\s+code)?\s+(?:to\s+)?)?"
    r"(?P<trigger>implement|make\s+the\s+change|make\s+those\s+changes)"
    r"(?P<request>\b.*)?$",
    re.IGNORECASE | re.DOTALL,
)
_LLM_PROVIDER_NAMES = frozenset(
    {
        "anthropic",
        "openai",
        "openrouter",
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
