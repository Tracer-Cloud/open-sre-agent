"""Handle follow-up questions by grounding them against the previous investigation."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.error_handling.exception_reporting import report_exception
from app.cli.interactive_shell.prompt_logging import LlmRunInfo
from app.cli.interactive_shell.token_accounting import build_llm_run_info
from app.cli.interactive_shell.ui import DIM, ERROR, STREAM_LABEL_ANSWER, WARNING, stream_to_console

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.cli.interactive_shell.runtime.session import ReplSession

# Keep at most this many Q&A pairs in follow-up history (matches cli_agent cap).
_MAX_FOLLOW_UP_TURNS = 12


def _format_followup_history(session: ReplSession) -> str:
    """Render prior follow-up Q&A pairs for the current investigation."""
    if not session.follow_up_messages:
        return ""
    lines: list[str] = []
    cap = _MAX_FOLLOW_UP_TURNS * 2
    for role, content in session.follow_up_messages[-cap:]:
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _record_follow_up_turn(session: ReplSession, question: str, answer: str) -> None:
    """Append a follow-up Q&A pair to follow_up_messages (scoped to current investigation)."""
    session.follow_up_messages.append(("user", question))
    session.follow_up_messages.append(("assistant", answer))
    cap = _MAX_FOLLOW_UP_TURNS * 2
    if len(session.follow_up_messages) > cap:
        session.follow_up_messages[:] = session.follow_up_messages[-cap:]


def _summarize_evidence(evidence: Any) -> list[str]:
    """Render a short evidence preview for the follow-up prompt.

    ``AgentState.evidence`` is a ``dict[str, Any]`` keyed by evidence id, but
    we accept list/other shapes defensively so an unexpected value doesn't
    silently drop all grounding context.
    """
    if isinstance(evidence, dict):
        sample_keys = list(evidence)[:3]
        sample = {key: evidence[key] for key in sample_keys}
        return [
            f"Evidence items: {len(evidence)}",
            "Evidence keys: " + ", ".join(map(str, sample_keys)),
            "Sample evidence:\n" + json.dumps(sample, indent=2, default=str)[:1500],
        ]
    if isinstance(evidence, list):
        return [
            f"Evidence items: {len(evidence)}",
            "Sample evidence:\n" + json.dumps(evidence[:3], indent=2, default=str)[:1500],
        ]
    return [
        f"Evidence type: {type(evidence).__name__}",
        f"Evidence summary:\n{str(evidence)[:1500]}",
    ]


def _summarize_last_state(state: dict[str, Any]) -> str:
    """Produce a compact text summary of the previous investigation for grounding."""
    parts: list[str] = []
    alert_name = state.get("alert_name")
    if alert_name:
        parts.append(f"Alert: {alert_name}")
    root_cause = state.get("root_cause")
    if root_cause:
        parts.append(f"Root cause: {root_cause}")
    problem_md = state.get("problem_md") or ""
    if problem_md:
        parts.append(f"Problem summary:\n{problem_md[:2000]}")
    slack_message = state.get("slack_message") or ""
    if slack_message:
        parts.append(f"Report:\n{slack_message[:2000]}")
    evidence = state.get("evidence")
    if evidence:
        try:
            parts.extend(_summarize_evidence(evidence))
        except (TypeError, ValueError) as exc:
            # Serialization can fail on exotic evidence values; tell the LLM
            # the context was withheld rather than silently dropping it.
            _logger.warning("could not serialize evidence for follow-up: %s", exc)
            parts.append("(evidence present but could not be serialized for grounding)")
    return "\n\n".join(parts) or "(no prior investigation details available)"


def answer_follow_up(
    question: str,
    session: ReplSession,
    console: Console,
) -> LlmRunInfo | None:
    """Answer a follow-up question about the previous investigation.

    The answer is grounded strictly in the prior investigation state.
    """
    if session.last_state is None:
        console.print(
            f"[{WARNING}]no prior investigation in this session.[/] "
            "describe an alert first, then ask follow-up questions about it."
        )
        return None

    try:
        from app.services.llm_client import get_llm_for_reasoning
    except Exception as exc:
        report_exception(exc, context="interactive_shell.follow_up.import")
        console.print(f"[{ERROR}]LLM client unavailable:[/] {escape(str(exc))}")
        return None

    context = _summarize_last_state(session.last_state)
    history = _format_followup_history(session)
    history_block = f"--- Prior follow-up conversation ---\n{history}\n\n" if history else ""
    prompt = (
        "You are an SRE assistant answering follow-up questions about a prior "
        "incident investigation. Use only the provided investigation context. "
        "If the context does not contain the answer, say so plainly. "
        "Keep answers concise and concrete.\n\n"
        f"--- Prior investigation ---\n{context}\n\n"
        f"{history_block}"
        f"--- Follow-up question ---\n{question}"
    )

    try:
        client = get_llm_for_reasoning()
        started = time.monotonic()
        response_text = stream_to_console(
            console,
            label=STREAM_LABEL_ANSWER,
            chunks=client.invoke_stream(prompt),
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]· cancelled[/]")
        return None
    except Exception as exc:
        report_exception(exc, context="interactive_shell.follow_up.stream")
        console.print(f"[{ERROR}]follow-up failed:[/] {escape(str(exc))}")
        return None

    if response_text:
        _record_follow_up_turn(session, question, response_text)

    return build_llm_run_info(
        session=session,
        prompt=prompt,
        response_text=response_text,
        started=started,
        client=client,
    )


__all__ = ["answer_follow_up"]
