"""Format terminal-turn outcomes for prompt-log and PostHog analytics."""

from __future__ import annotations

_ANALYTICS_OUTPUT_MAX_CHARS = 8_000

# Slash commands whose handlers attach to the real TTY (wizards, pickers). Analytics
# should record structured success/failure, not full interactive transcripts.
_INTERACTIVE_WIZARD_SLASH_ROOTS: frozenset[str] = frozenset(
    {
        "/onboard",
        "/auth",
        "/login",
        "/integrations",
        "/mcp",
    }
)
_INTERACTIVE_WIZARD_SLASH_PATHS: frozenset[str] = frozenset(
    {
        "/integrations setup",
        "/integrations remove",
        "/mcp connect",
        "/mcp disconnect",
        "/auth login",
        "/auth logout",
    }
)

# Slash commands where console capture is noisy or redundant. Substantive output for
# investigations is stored on the companion ``alert`` history row instead.
_SUMMARY_ONLY_SLASH_ROOTS: frozenset[str] = frozenset(
    {
        "/",
        "/help",
        "/?",
        "/investigate",
    }
)


def slash_command_is_interactive_wizard(command_line: str) -> bool:
    """True when ``command_line`` names a multi-step TTY wizard or picker."""
    stripped = command_line.strip()
    if not stripped.startswith("/"):
        return False
    parts = stripped.split()
    root = parts[0].lower()
    if root in _INTERACTIVE_WIZARD_SLASH_ROOTS and len(parts) == 1:
        return True
    if len(parts) >= 2:
        path = f"{root} {parts[1].lower()}"
        if path in _INTERACTIVE_WIZARD_SLASH_PATHS:
            return True
    return False


def slash_command_is_summary_only(command_line: str) -> bool:
    """True when analytics should omit captured console text for this slash command."""
    stripped = command_line.strip()
    if not stripped.startswith("/"):
        return False
    parts = stripped.split()
    root = parts[0].lower()
    if root in _SUMMARY_ONLY_SLASH_ROOTS:
        return True
    return slash_command_is_interactive_wizard(command_line)


def truncate_analytics_text(text: str, *, max_chars: int = _ANALYTICS_OUTPUT_MAX_CHARS) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 20].rstrip()}… [truncated]"


def format_wizard_cli_outcome(args: list[str], *, exit_code: int | None) -> str:
    """Structured outcome for delegated interactive CLI wizards (e.g. ``/onboard``)."""
    command = " ".join(["opensre", *args]).strip()
    if exit_code is None:
        return f"{command}: interactive wizard cancelled"
    if exit_code == 0:
        return f"{command}: interactive wizard completed successfully"
    return f"{command}: interactive wizard failed (exit {exit_code})"


def _investigation_report_excerpt(final_state: dict[str, object]) -> str:
    sections: list[str] = []
    root = final_state.get("root_cause")
    if isinstance(root, str) and root.strip():
        sections.append(f"Root cause: {root.strip()}")
    for key in ("problem_md", "slack_message"):
        body = final_state.get(key)
        if isinstance(body, str) and body.strip():
            sections.append(body.strip())
            break
    return "\n\n".join(sections)


def format_investigation_outcome(
    target: str,
    *,
    final_state: dict[str, object] | None = None,
    background: bool = False,
    error_message: str = "",
    status: str | None = None,
) -> str:
    """Human-readable investigation outcome body for analytics."""
    label = target.strip() or "investigation"
    if background:
        return f"investigation started in background: {label}"
    if status == "cancelled":
        return f"investigation_cancelled ({label}): aborted by user"
    if status == "failed" or (final_state is None and error_message):
        reason = error_message.strip() or "investigation failed"
        return truncate_analytics_text(f"investigation_failed ({label}):\n{reason}")
    if final_state is None:
        return f"investigation_failed ({label}): investigation did not complete"
    excerpt = _investigation_report_excerpt(final_state)
    if excerpt:
        return truncate_analytics_text(f"investigation completed ({label}):\n{excerpt}")
    return f"investigation completed: {label}"


def format_investigation_terminal_outcome(
    command_line: str,
    *,
    target: str,
    ok: bool,
    final_state: dict[str, object] | None = None,
    background: bool = False,
    error_message: str = "",
    status: str | None = None,
) -> str:
    """Two-line terminal analytics payload for ``/investigate`` turns."""
    if background:
        return format_investigation_outcome(target, background=True)
    resolved_status = status or ("succeeded" if ok and final_state is not None else "failed")
    if resolved_status == "completed":
        resolved_status = "succeeded"
    slash_status = {
        "succeeded": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(resolved_status, "failed")
    prefix = f"slash {command_line.strip()} ({slash_status})"
    body = format_investigation_outcome(
        target,
        final_state=final_state,
        error_message=error_message,
        status="cancelled"
        if resolved_status == "cancelled"
        else ("failed" if resolved_status == "failed" else None),
    )
    return truncate_analytics_text(f"{prefix}\n{body}")


def format_terminal_turn_outcome(
    command_line: str,
    *,
    kind: str,
    ok: bool,
    captured_output: str = "",
    outcome_hint: str | None = None,
    include_captured_on_summary_only: bool = False,
) -> str:
    """Build the analytics payload for one handled terminal turn."""
    if outcome_hint and outcome_hint.strip():
        return truncate_analytics_text(outcome_hint.strip())

    status = "succeeded" if ok else "failed"
    prefix = f"{kind} {command_line.strip()} ({status})"

    summary_only = kind == "slash" and slash_command_is_summary_only(command_line)
    if summary_only and not include_captured_on_summary_only:
        return prefix

    if captured_output:
        return truncate_analytics_text(f"{prefix}\n{captured_output}")
    return prefix
