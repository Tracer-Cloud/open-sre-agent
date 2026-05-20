"""LLM-backed structured action planner for interactive-shell input."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from typing import Any

from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.intent_parser import (
    split_prompt_clauses,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.interaction_models import (
    PlannedAction,
    default_target_surface,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.slash_commands.deterministic_action_mapper import (
    map_actions_with_unhandled,
)
from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.tool_registry import (
    ACTION_KIND_TO_TOOL,
    REGISTRY,
)

logger = logging.getLogger(__name__)

_LOCAL_LLAMA_CONNECT_RE = re.compile(
    r"\b(?:please\s+)?(?:connect|use)\b.{0,40}\b(?:to\s+)?(?:local\s+)?llama\b",
    re.IGNORECASE,
)
_RICH_PASTED_INCIDENT_RE = re.compile(
    r"^(?:.*\n){1,}.*\b(?:service|region)\s*:",
    re.IGNORECASE | re.DOTALL,
)
_ALERT_SYMPTOM_RE = re.compile(
    r"\b(?:cpu|spiking|spike|latency|error|5\d\d|pods?|firing|checkout|database)\b",
    re.IGNORECASE,
)

_MAX_TEXT_LEN = 512
_USER_TEMPLATE = "USER MESSAGE (literal): <<<{text}>>>"
_UNHANDLED_MARKER = "UNHANDLED:"
_OPENAI_STYLE_PROVIDERS = frozenset(
    {"openai", "openrouter", "gemini", "nvidia", "minimax", "ollama"}
)
_TOOL_TO_ACTION_KIND = {tool: kind for kind, tool in ACTION_KIND_TO_TOOL.items()}

_SYSTEM_PROMPT_BASE = """You plan actions for the OpenSRE interactive shell.

Use tool calls whenever the user explicitly asks to run, show, execute,
launch, cancel, connect, switch, or start an operation. Compound requests
joined by "and", "and then", "then", etc. should emit one tool call per
component action, in the order requested.

Interpret "kick off sample alert", "run sample alert", or "trigger sample alert"
(including variants like "kick off a sample alert investigation") as the
alert_sample tool with template="generic", not investigation_start.
If this appears as one clause in a compound request, still emit alert_sample
for that clause in sequence.

If the user asks for a slash action and then asks to investigate/send quoted
follow-up text (for example: connect with /remote and then investigate "hello world"),
emit TWO actions in order:
1) slash_invoke for the slash command
2) investigation_start with alert_text set to the quoted follow-up text.

Example mapping for sequence + sample alert:
- Input: "run /health and then kick off a sample alert investigation"
- Tool calls (in order): slash_invoke("/health"), alert_sample(template="generic")

Example mapping for compound slash commands:
- Input: "check the health of my opensre and then show me all connected services"
- Tool calls (in order): slash_invoke("/health"), slash_invoke("/list", args=["integrations"])
  ("connected services/integrations" → /list integrations)

For operational REPL requests, prefer slash_invoke and choose the command
from the slash catalog below. Each entry lists when to use it and when not to.
Other tools:
- llm_set_provider — switch provider when target is an exact provider name
- alert_sample — run a sample alert (template="generic")
- investigation_start — investigate pasted alert text or free-form alert body
- synthetic_run — run synthetic benchmark scenario by id
- cli_exec — run opensre <subcommand> when user explicitly says opensre
  (payload without the opensre  prefix)
- task_cancel — cancel a background task by id or kind
- shell_run — narrowly scoped local diagnostic shell commands
- code_implement — code implementation workflow
- assistant_handoff — informational/conversational requests (docs, greetings,
  pasted alerts for analysis discussion, follow-ups, vague ops questions)
- mark_unhandled — flag a clause that cannot be mapped (see below)

Never use shell_run for OpenSRE product requests like "show integration details",
"list connected services", "show model/provider", or docs/how-to questions.
Those are assistant_handoff or slash/cli operations, not shell diagnostics.
Use shell_run only when the user explicitly asks for a local shell command
(for example: backticks, command names, or "run command ...").

If ANY clause in the user's request (clauses split by "and", "and then",
"then", ",", or ";") is one of the following:
- chatty filler ("sing a song", "tell me a joke", "make me coffee",
  "say hi back", "wish me luck", "be nice", "compliment me", "rap")
- nonsensical or off-topic (anything not related to SRE/observability/
  infrastructure)
- ambiguous (cannot be confidently mapped to an OpenSRE operation)
- non-executable (a how-to question embedded in a compound prompt)

… you MUST also call the mark_unhandled tool with a short reason
describing the unmatched clause. Do this even when the other clause(s)
are perfectly executable. Without it, the partially-handled prompt is
silently treated as fully handled and the unmatched clause is dropped —
a bug, not the desired behavior. NEVER silently drop a clause.

Example: for the prompt "show me connected services and sing a song"
you MUST emit EXACTLY two tool calls in the same response:
1. slash_invoke (command="/list", args=["integrations"])
2. mark_unhandled (reason="'sing a song' is chatty filler, not an
   executable OpenSRE operation.")

If the entire request is informational or conversational (a how-to question,
greeting like "hi"/"hello"/"hey", an alert blob pasted as JSON or free text,
an incident description, a follow-up like "why did it fail?" / "what caused
the spike?", or a vague operational question like "why is the database
slow?"), ALWAYS call the assistant_handoff tool with a concise handoff
content. Do NOT respond with text-only "UNHANDLED:" output in this
case — the planner only forwards actions emitted through tool calls, so
plain text is silently dropped and the user sees a fail-closed prompt
instead of the assistant's reply.
"""


def _system_prompt() -> str:
    from app.cli.interactive_shell.command_registry.slash_catalog import (
        build_slash_command_specs,
        format_slash_catalog_text,
    )

    catalog = format_slash_catalog_text(build_slash_command_specs(), compact=True)
    return f"{_SYSTEM_PROMPT_BASE}\n\n## Slash command catalog\n\n{catalog}\n"


def _sanitise_text(text: str) -> str:
    sanitised = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    sanitised = re.sub(r"<{3,}|>{3,}", " ", sanitised)
    return sanitised[:_MAX_TEXT_LEN]


def _tool_specs_for_provider(session: Any) -> list[dict[str, Any]]:
    from app.cli.interactive_shell.runtime.session import ReplSession
    from app.config import resolve_llm_settings

    provider = resolve_llm_settings().provider
    base_specs = REGISTRY.tool_specs_for_llm(session or ReplSession())
    if provider in _OPENAI_STYLE_PROVIDERS:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["input_schema"],
                },
            }
            for spec in base_specs
        ]
    return base_specs


def _call_llm(sanitised_text: str, session: Any) -> str | None:
    try:
        from app.services.llm_client import get_llm_for_classification
    except Exception as exc:
        # Surface at WARNING (not DEBUG): a missing/broken LLM client makes
        # every planner call silently fail closed, and DEBUG-only logs hid a
        # full-fleet outage in the past. Callers still get None and the
        # fail-closed UX, but operators see why in normal log output.
        logger.warning(
            "llm_action_planner: LLM client import failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return None

    prompt = f"{_system_prompt()}\n\n{_USER_TEMPLATE.format(text=sanitised_text)}"
    try:
        client = get_llm_for_classification().bind_tools(_tool_specs_for_provider(session))
        response = client.invoke(prompt)
        return response.content.strip()
    except Exception as exc:
        logger.warning(
            "llm_action_planner: LLM call failed (%s): %s",
            type(exc).__name__,
            exc,
        )
        return None


def _normalize_tool_args(
    kind: str,
    args: dict[str, Any],
    *,
    session: Any | None = None,
) -> dict[str, Any] | None:
    if kind == "slash":
        command = str(args.get("command", "")).strip()
        raw_args = args.get("args")
        parsed_args = [str(item).strip() for item in raw_args] if isinstance(raw_args, list) else []
        # Bare ``/integrations`` (no args) is a paraphrase the LLM emits for
        # "list integrations" intents — rewrite to the canonical
        # ``/list integrations`` form. Crucially, do NOT rewrite when
        # ``parsed_args`` is non-empty: a tool call like
        # ``/integrations show datadog`` carries the operation+service in
        # its args and must reach the per-service ``configured`` filter
        # below; rewriting unconditionally drops those args and makes the
        # filter unreachable (regression that surfaced as
        # ``204-integration-show-unconfigured-fail-closed``).
        if command == "/integrations" and not parsed_args:
            command = "/list"
            parsed_args = ["integrations"]
        if not command.startswith("/"):
            return None
        from app.cli.interactive_shell.commands import SLASH_COMMANDS

        if command.split(maxsplit=1)[0].lower() not in SLASH_COMMANDS:
            return None
        capability_map = getattr(session, "available_capabilities", {}) or {}
        available_slash = capability_map.get("slash_commands")
        if (
            isinstance(available_slash, tuple)
            and available_slash
            and command.split(maxsplit=1)[0] not in set(available_slash)
        ):
            return None
        configured_known = bool(getattr(session, "configured_integrations_known", False))
        configured = set(getattr(session, "configured_integrations", ()) or ())
        if configured_known and command == "/integrations" and parsed_args:
            op = parsed_args[0].lower()
            service = parsed_args[1].lower() if len(parsed_args) > 1 else ""
            if op in {"show", "verify", "remove"} and service and service not in configured:
                return None
        return {"command": command, "args": parsed_args}
    if kind == "llm_provider":
        target = str(args.get("target", args.get("provider", ""))).strip()
        if not target:
            return None
        from app.cli.wizard.config import PROVIDER_BY_VALUE

        if target.lower() in PROVIDER_BY_VALUE:
            return {"provider": target.lower()}
        return {"provider": target}
    if kind == "shell":
        command = str(args.get("command", "")).strip()
        return {"command": command} if command else None
    if kind == "sample_alert":
        template = str(args.get("template", "")).strip().lower()
        if template != "generic":
            return None
        return {"template": template}
    if kind == "investigation":
        alert_text = str(args.get("alert_text", "")).strip()
        return {"alert_text": alert_text} if alert_text else None
    if kind == "synthetic_test":
        suite = str(args.get("suite", "")).strip()
        scenario = str(args.get("scenario", "")).strip()
        if not suite or not scenario:
            return None
        capability_map = getattr(session, "available_capabilities", {}) or {}
        available_suites = capability_map.get("synthetic_suites")
        if (
            isinstance(available_suites, tuple)
            and available_suites
            and suite not in set(available_suites)
        ):
            return None
        from app.cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
            list_rds_postgres_scenarios,
        )

        available = set(list_rds_postgres_scenarios())
        if scenario != "all" and scenario not in available:
            return None
        return {"suite": suite, "scenario": scenario}
    if kind == "task_cancel":
        target = str(args.get("target", "")).strip()
        if not target:
            return None
        if target in {"task", "synthetic_test"}:
            return {"target": target}
        if re.fullmatch(r"[A-Za-z0-9_-]{3,}", target):
            return {"target": target}
        return None
    if kind == "cli_command":
        payload = str(args.get("payload", "")).strip()
        if not payload or payload.lower().startswith("opensre "):
            return None
        capability_map = getattr(session, "available_capabilities", {}) or {}
        available_cli = capability_map.get("cli_commands")
        if isinstance(available_cli, tuple) and available_cli:
            command_name = payload.split(maxsplit=1)[0]
            if command_name not in set(available_cli):
                return None
        return {"payload": payload}
    if kind == "implementation":
        task = str(args.get("task", "")).strip()
        return {"task": task} if task else None
    if kind == "assistant_handoff":
        content = str(args.get("content", "")).strip()
        return {"content": content} if content else None
    return None


def _content_from_tool_args(kind: str, args: dict[str, Any]) -> str:
    if kind == "slash":
        command = str(args.get("command", "")).strip()
        parsed_args = args.get("args")
        extras = (
            [str(item).strip() for item in parsed_args] if isinstance(parsed_args, list) else []
        )
        return " ".join([command, *extras]) if extras else command
    if kind == "synthetic_test":
        return f"{str(args.get('suite', '')).strip()}:{str(args.get('scenario', '')).strip()}"
    if kind == "cli_command":
        return str(args.get("payload", "")).strip()
    if kind == "sample_alert":
        return str(args.get("template", "")).strip()
    if kind == "investigation":
        return str(args.get("alert_text", "")).strip()
    if kind == "shell":
        return str(args.get("command", "")).strip()
    if kind == "task_cancel":
        return str(args.get("target", "")).strip()
    if kind == "implementation":
        return str(args.get("task", "")).strip()
    if kind == "llm_provider":
        return str(args.get("target", args.get("provider", ""))).strip()
    return str(args.get("content", "")).strip()


def _parse_tool_plan(
    raw: str, *, session: Any | None = None
) -> tuple[list[PlannedAction], bool] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Text-only answer: fail closed and treat as unhandled.
        return [], bool(raw.strip())

    if not isinstance(data, dict):
        return None

    raw_calls = data.get("tool_calls")
    text = str(data.get("text", "")).strip()
    has_unhandled = text.startswith(_UNHANDLED_MARKER)
    if not isinstance(raw_calls, list):
        return [], bool(text)
    # OpenAI-style providers clear the ``text`` field whenever a model
    # emits ``tool_calls``, so the prompt instructs the LLM to flag
    # partial handling either via:
    #   (a) a dedicated ``mark_unhandled`` tool call (preferred — easier
    #       for the LLM to follow than content conventions), or
    #   (b) an ``assistant_handoff`` whose ``content`` starts with the
    #       literal "UNHANDLED:" token (fallback).
    # Detect both signals here. Without this, compound prompts like
    # "show me connected services AND sing a song" silently lose the
    # nonsense clause and never fail-closed.
    if not has_unhandled:
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_name = str(call.get("name", "")).strip()
            if call_name == "mark_unhandled":
                has_unhandled = True
                break
            if call_name == "assistant_handoff":
                call_args = call.get("arguments")
                if isinstance(call_args, dict) and str(
                    call_args.get("content", "")
                ).lstrip().startswith(_UNHANDLED_MARKER):
                    has_unhandled = True
                    break

    actions: list[PlannedAction] = []
    for idx, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("name", "")).strip()
        kind = _TOOL_TO_ACTION_KIND.get(tool_name)
        if kind is None:
            continue
        raw_args = call.get("arguments")
        args = raw_args if isinstance(raw_args, dict) else {}
        normalized_args = _normalize_tool_args(kind, args, session=session)
        if normalized_args is None:
            has_unhandled = True
            continue
        actions.append(
            PlannedAction(
                kind=kind,  # type: ignore[arg-type]
                content=_content_from_tool_args(kind, normalized_args),
                position=idx,
                source="llm",
                confidence=1.0,
                rationale=None,
                target_surface=default_target_surface(kind),  # type: ignore[arg-type]
                args=normalized_args,
            )
        )

    return actions, has_unhandled


def _as_llm_sourced(actions: list[PlannedAction]) -> list[PlannedAction]:
    """Reconciled plans still flow through the LLM planner entrypoint."""
    return [replace(action, source="llm") for action in actions]


def _fail_closed_vague_local_model(message: str) -> tuple[list[PlannedAction], bool] | None:
    if _LOCAL_LLAMA_CONNECT_RE.search(message):
        return [], True
    return None


def _reconcile_compound_actions(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    """Prefer deterministic multi-clause plans when the LLM under-plans compounds."""
    if len(split_prompt_clauses(message)) <= 1:
        return actions, has_unhandled
    if actions and all(action.kind == "assistant_handoff" for action in actions):
        return actions, has_unhandled

    det_actions, det_unhandled = map_actions_with_unhandled(message)
    if not det_actions or len(det_actions) <= len(actions):
        return actions, has_unhandled
    return _as_llm_sourced(det_actions), det_unhandled


def _upgrade_handoff_to_incident(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    """Map symptom-style alerts to investigation when the LLM only handoffs."""
    if len(split_prompt_clauses(message)) != 1:
        return actions, has_unhandled
    if not actions or not all(action.kind == "assistant_handoff" for action in actions):
        return actions, has_unhandled
    if "?" in message or re.search(r"\bhow\s+(?:do|to)\b", message, re.IGNORECASE):
        return actions, has_unhandled
    if not _ALERT_SYMPTOM_RE.search(message):
        return actions, has_unhandled
    alert_text = message.strip()
    return [
        PlannedAction(
            kind="investigation",
            content=alert_text,
            position=0,
            source="llm",
            target_surface="investigation",
            args={"alert_text": alert_text},
        )
    ], False


def _fail_closed_unconfigured_integration_detail(
    message: str,
    session: Any | None,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    if session is None or not bool(getattr(session, "configured_integrations_known", False)):
        return actions, has_unhandled
    configured = set(getattr(session, "configured_integrations", ()) or ())
    lowered = message.lower()
    for service in ("datadog", "grafana", "sentry", "posthog", "clickhouse"):
        if (
            service in lowered
            and service not in configured
            and re.search(r"\b(show|details|verify|remove|integration)\b", lowered)
        ):
            return [
                    PlannedAction(
                        kind="assistant_handoff",
                        content=f"integration_details:{service}_unconfigured",
                        position=0,
                        source="llm",
                    )
                ], False
    return actions, has_unhandled


def _coerce_rich_paste_handoff(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
) -> tuple[list[PlannedAction], bool]:
    """Rich pasted incidents should hand off, not auto-launch investigation."""
    if _RICH_PASTED_INCIDENT_RE.search(message) is None:
        return actions, has_unhandled
    if not actions or not all(action.kind == "investigation" for action in actions):
        return actions, has_unhandled
    if re.search(r"\bhow\s+(?:do|to)\b", message, re.IGNORECASE):
        return actions, has_unhandled
    return [
        PlannedAction(
            kind="assistant_handoff",
            content="incident_description:rich_context",
            position=0,
            source="llm",
        )
    ], True


def _finalize_planner_result(
    message: str,
    actions: list[PlannedAction],
    has_unhandled: bool,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool]:
    early = _fail_closed_vague_local_model(message)
    if early is not None:
        return early
    actions, has_unhandled = _fail_closed_unconfigured_integration_detail(
        message, session, actions, has_unhandled
    )
    if not actions and has_unhandled:
        return actions, has_unhandled
    actions, has_unhandled = _reconcile_compound_actions(message, actions, has_unhandled)
    actions, has_unhandled = _upgrade_handoff_to_incident(message, actions, has_unhandled)
    return _coerce_rich_paste_handoff(message, actions, has_unhandled)


def plan_actions_with_llm(
    message: str,
    *,
    session: Any | None = None,
) -> tuple[list[PlannedAction], bool] | None:
    """Plan actions from *message* using native tool-calling."""
    sanitised = _sanitise_text(message.strip())
    early = _fail_closed_vague_local_model(sanitised)
    if early is not None:
        return early
    raw = _call_llm(sanitised, session)
    if raw is None:
        return None
    parsed = _parse_tool_plan(raw, session=session)
    if parsed is None:
        return None
    actions, has_unhandled = parsed
    return _finalize_planner_result(sanitised, actions, has_unhandled, session=session)


__all__ = ["plan_actions_with_llm"]
