"""Handle follow-up questions by grounding them against the previous investigation."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape

from app.cli.interactive_shell.prompt_logging import LlmRunInfo
from app.cli.interactive_shell.ui import DIM, ERROR, STREAM_LABEL_ANSWER, WARNING, stream_to_console
from app.cli.support.exception_reporting import report_exception

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.cli.interactive_shell.runtime.session import ReplSession


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
    prompt = (
        "You are an SRE assistant answering a follow-up question about a prior "
        "incident investigation that you just completed. Use only the provided "
        "investigation context. If the context does not contain the answer, say so "
        "plainly. Keep the answer concise and concrete.\n\n"
        f"--- Prior investigation ---\n{context}\n\n"
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
    return LlmRunInfo(
        model=_resolve_model_name(client),
        provider=_resolve_provider_name(client),
        latency_ms=int((time.monotonic() - started) * 1000),
        response_text=response_text,
    )


def _resolve_model_name(client: object) -> str | None:
    value = getattr(client, "_model", None)
    return value if isinstance(value, str) and value else None


def _resolve_provider_name(client: object) -> str | None:
    provider_label = getattr(client, "_provider_label", None)
    if isinstance(provider_label, str) and provider_label:
        return provider_label.strip().lower().replace(" ", "_")
    name = type(client).__name__.lower()
    if "openai" in name:
        return "openai"
    if "bedrock" in name:
        return "bedrock"
    if "cli" in name:
        return "cli"
    if "anthropic" in name or "llmclient" in name:
        return "anthropic"
    return None


__all__ = ["answer_follow_up"]
