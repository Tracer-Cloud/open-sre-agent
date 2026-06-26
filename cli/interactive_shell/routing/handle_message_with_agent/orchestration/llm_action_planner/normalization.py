"""Tool-call argument normalization and content projection."""

from __future__ import annotations

import re
from typing import Any


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
        if command == "/integrations" and not parsed_args:
            parsed_args = ["list"]
        if not command.startswith("/"):
            return None

        from cli.interactive_shell.command_registry import SLASH_COMMANDS

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

        from cli.wizard.config import PROVIDER_BY_VALUE

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

        from cli.interactive_shell.routing.handle_message_with_agent.orchestration.synthetic_scenarios import (
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
