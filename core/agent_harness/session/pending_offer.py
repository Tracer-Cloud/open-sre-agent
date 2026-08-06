"""Structured offers awaiting a bare affirmative (yes / sure / …).

Schedule confirmation must not scrape Want-me-to prose. The turn that proposes
the schedule writes a :class:`PendingScheduleOffer` onto the session; ``yes``
reads that object and becomes a literal ``/cron add …`` with no regex.

Investigation offers follow the same pattern: when the assistant closer matches
:meth:`PendingInvestigationOffer.want_me_to_body`, the turn arms a
:class:`PendingInvestigationOffer` (alert_text from the diagnostic turn +
evidence — not from prose). ``yes`` expands to a literal
``/investigate alert:…`` slash — the same sanctioned deterministic path as
schedule ``/cron`` (no separate static tool-call bypass).

Both offer types implement :class:`DispatchablePendingOffer` so expand / confirm /
consume share one path (open for a third offer kind without editing the
orchestrator).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.agent_harness.session.want_me_to import (
    offer_from_assistant_content,
    replace_want_me_to_body,
)

# Common morning-report defaults → human cadence labels (exact cron match only).
_CADENCE_LABELS: dict[str, str] = {
    "0 8 * * 1-5": "every weekday at 8am",
    "0 9 * * 1-5": "every weekday at 9am",
    "0 7 * * 1-5": "every weekday at 7am",
}

# Canonical Want-me-to body for a full-investigation offer (INTERACTION_RULES).
_INVESTIGATION_WANT_ME_TO_BODY = "run a full investigation"
# Dogfood: models say "kick off a full investigation…" — still arm.
_FULL_INVESTIGATION_MARKER = "full investigation"
_SCHEDULE_OFFER_MARKERS = (
    "schedule this",
    "recurring",
    "/cron",
)

# Session attribute names that hold a pending affirmative (priority order for yes).
_PENDING_OFFER_ATTRS: tuple[str, ...] = (
    "pending_schedule_offer",
    "pending_investigation_offer",
)


@runtime_checkable
class DispatchablePendingOffer(Protocol):
    """Structured offer that expands a bare yes into a deterministic dispatch."""

    def to_dispatch_message(self) -> str:
        """User-message form the action driver executes without an LLM."""
        raise NotImplementedError

    def matches_expanded(self, expanded: str) -> bool:
        """True when ``expanded`` is this offer's dispatch message."""
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class PendingScheduleOffer:
    """A schedule the user has been offered and has not yet confirmed."""

    kind: str
    cron: str
    timezone: str
    provider: str
    chat_id: str = ""

    def to_slash_command(self) -> str:
        """Literal slash the action driver dispatches without an LLM round-trip."""
        args = [
            "add",
            "--kind",
            self.kind,
            "--cron",
            self.cron,
            "--tz",
            self.timezone,
            "--provider",
            self.provider,
        ]
        chat = self.chat_id.strip()
        if chat:
            args.extend(["--chat-id", chat])
        # shlex.quote the parts: the five-field cron expression is one argument,
        # and the dispatcher tokenises this text before the CLI ever sees it.
        return "/cron " + " ".join(shlex.quote(arg) for arg in args)

    def to_dispatch_message(self) -> str:
        return self.to_slash_command()

    def matches_expanded(self, expanded: str) -> bool:
        return isinstance(expanded, str) and expanded.startswith("/cron ")

    def want_me_to_body(self) -> str:
        """Canonical closer body (no leading Want me to:) for the assistant to show."""
        cadence = _CADENCE_LABELS.get(self.cron.strip(), f"on cron {self.cron}")
        dest = self.provider
        chat = self.chat_id.strip()
        if chat:
            dest = f"{self.provider} ({chat})"
        return f"schedule this as a recurring {self.kind} {cadence} to {dest}"


@dataclass(frozen=True, slots=True)
class PendingInvestigationOffer:
    """A full investigation the user has been offered and has not yet confirmed."""

    alert_text: str

    def to_slash_command(self) -> str:
        """Literal slash the action driver dispatches without an LLM round-trip.

        Uses the existing ``/investigate alert:…`` form (also used by Discord
        slash payloads) so accept rides the sanctioned literal-``/slash`` path —
        same reliability bar as :meth:`PendingScheduleOffer.to_slash_command`.
        """
        alert = self.alert_text.strip()
        # Quote the whole ``alert:…`` token so spaces/quotes in the body survive
        # ``shlex.split`` in ``_literal_slash_tool_call`` / ``dispatch_slash``.
        return "/investigate " + shlex.quote(f"alert:{alert}")

    def to_dispatch_message(self) -> str:
        return self.to_slash_command()

    def matches_expanded(self, expanded: str) -> bool:
        return isinstance(expanded, str) and expanded.startswith("/investigate ")

    def want_me_to_body(self) -> str:
        """Canonical closer body (no leading Want me to:)."""
        return _INVESTIGATION_WANT_ME_TO_BODY


def parse_investigation_accept_message(message: str) -> str | None:
    """Return alert_text when ``message`` is a structured investigate-accept slash."""
    raw = message if isinstance(message, str) else ""
    stripped = raw.strip()
    if not stripped.startswith("/investigate"):
        return None
    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        parts = stripped.split()
    if len(parts) < 2:
        return None
    target = parts[1]
    if not target.lower().startswith("alert:"):
        return None
    alert = target.split(":", 1)[1].strip()
    return alert or None


def assistant_offers_full_investigation(assistant_text: str) -> bool:
    """True when the assistant Want-me-to closer offers a full investigation.

    Accepts the canonical ``run a full investigation`` and close variants such
    as ``kick off a full investigation…`` (dogfood). Rejects vendor
    ``investigate X in Slack`` closers and schedule-only offers.
    """
    offer = offer_from_assistant_content(assistant_text)
    if not offer:
        return False
    lowered = offer.lower()
    if _FULL_INVESTIGATION_MARKER not in lowered:
        return False
    return not any(marker in lowered for marker in _SCHEDULE_OFFER_MARKERS)


def is_schedule_only_want_me_to(assistant_text: str) -> bool:
    """True when the closer is a schedule offer (leave it for PendingScheduleOffer)."""
    offer = offer_from_assistant_content(assistant_text)
    if not offer:
        return False
    lowered = offer.lower()
    if _FULL_INVESTIGATION_MARKER in lowered:
        return False
    return any(marker in lowered for marker in _SCHEDULE_OFFER_MARKERS)


def ensure_canonical_investigation_closer(assistant_text: str) -> str:
    """Force an existing Want-me-to closer to the canonical investigate body.

    Dogfood failure: dual paste-alert / ``/integrations setup`` menus left
    ``yes`` re-gathering or opening setup instead of ``/investigate alert:…``.
    Non-investigation options are dropped (not rephrased above the closer) so
    the user-visible offer matches what bare ``yes`` accepts. A reply with no
    Want-me-to closer is returned unchanged — the assistant may legitimately
    skip the offer. Schedule-only closers are left untouched.
    """
    text = assistant_text if isinstance(assistant_text, str) else ""
    offer = offer_from_assistant_content(text)
    if offer is None or is_schedule_only_want_me_to(text):
        return text
    return replace_want_me_to_body(text, _INVESTIGATION_WANT_ME_TO_BODY)


_MAX_EVIDENCE_LINE_CHARS = 160
_MAX_EVIDENCE_LINES = 8


def _compact_evidence_lines(observation: str) -> list[str]:
    """One line per gathered tool result: tool name plus a short outcome.

    The gather observation block is ``Tool:``/``Arguments:``/``Result:``
    paragraphs; raw result payloads (stack traces, JSON error dumps) read as
    noise to the intake alert parser, so only a compact outcome per tool goes
    into the alert text.
    """
    lines: list[str] = []
    for block in observation.split("\n\n"):
        if not block.startswith("Tool: "):
            continue
        name = block.split("\n", 1)[0][len("Tool: ") :].strip()
        _, _, result = block.partition("\nResult: ")
        outcome = " ".join(result.split()) or "(no result)"
        if len(outcome) > _MAX_EVIDENCE_LINE_CHARS:
            outcome = outcome[:_MAX_EVIDENCE_LINE_CHARS] + "…"
        lines.append(f"- {name}: {outcome}")
        if len(lines) >= _MAX_EVIDENCE_LINES:
            break
    return lines


def synthesize_investigation_alert_text(
    user_text: str,
    observation: str | None = None,
    *,
    max_evidence_chars: int = 800,
) -> str:
    """Build alert_text for investigation_start from the diagnostic turn."""
    base = (user_text or "").strip()
    evidence = (observation or "").strip()
    if not evidence:
        return base
    compact = _compact_evidence_lines(evidence)
    snippet = "\n".join(compact) if compact else evidence[:max_evidence_chars].rstrip()
    if len(snippet) > max_evidence_chars:
        snippet = snippet[:max_evidence_chars].rstrip() + "\n...[truncated]"
    if not base:
        return snippet
    return f"{base}\n\nEvidence gathered:\n{snippet}"


def _session_pending_offers(session: Any) -> list[tuple[str, DispatchablePendingOffer]]:
    """Return (attr, offer) pairs present on ``session``, in expand priority order."""
    found: list[tuple[str, DispatchablePendingOffer]] = []
    for attr in _PENDING_OFFER_ATTRS:
        offer = getattr(session, attr, None)
        if isinstance(offer, DispatchablePendingOffer):
            found.append((attr, offer))
    return found


def first_pending_offer(session: Any) -> DispatchablePendingOffer | None:
    """Highest-priority pending offer on the session, if any."""
    pairs = _session_pending_offers(session)
    return pairs[0][1] if pairs else None


def is_pending_offer_confirmation(session: Any, expanded: str) -> bool:
    """True when ``expanded`` confirms a pending offer currently on ``session``."""
    for _attr, offer in _session_pending_offers(session):
        if offer.matches_expanded(expanded):
            return True
    return False


def consume_confirmed_pending_offer(session: Any, expanded: str) -> bool:
    """Clear the pending offer that ``expanded`` confirmed. Returns True if cleared."""
    for attr, offer in _session_pending_offers(session):
        if offer.matches_expanded(expanded):
            setattr(session, attr, None)
            return True
    return False


def clear_competing_pending_offers(session: Any, *, keep_attr: str) -> None:
    """Clear every pending-offer attr except ``keep_attr`` (one affirmative at a time)."""
    for attr in _PENDING_OFFER_ATTRS:
        if attr == keep_attr:
            continue
        if hasattr(session, attr):
            setattr(session, attr, None)


def arm_pending_investigation_offer(
    session: Any,
    *,
    user_text: str,
    assistant_text: str,
    observation: str | None = None,
) -> PendingInvestigationOffer | None:
    """Arm session.pending_investigation_offer after a full-investigation closer.

    ``alert_text`` is synthesized from the diagnostic user text + gather
    evidence (structured), not scraped from assistant prose.
    """
    if not assistant_offers_full_investigation(assistant_text):
        return None
    alert = synthesize_investigation_alert_text(user_text, observation)
    if not alert:
        return None
    if not hasattr(session, "pending_investigation_offer"):
        return None
    offer = PendingInvestigationOffer(alert_text=alert)
    session.pending_investigation_offer = offer
    clear_competing_pending_offers(session, keep_attr="pending_investigation_offer")
    return offer


def finalize_gather_investigation_offer(
    session: Any,
    *,
    user_text: str,
    assistant_text: str,
    observation: str | None = None,
) -> tuple[str, PendingInvestigationOffer | None]:
    """Normalize gather closer + arm pending investigate accept.

    Returns ``(response_shown_to_user, offer_or_none)``.
    """
    normalized = ensure_canonical_investigation_closer(assistant_text)
    offer = arm_pending_investigation_offer(
        session,
        user_text=user_text,
        assistant_text=normalized,
        observation=observation,
    )
    _sync_last_assistant_message(session, normalized)
    return normalized, offer


def _sync_last_assistant_message(session: Any, text: str) -> None:
    messages = getattr(session, "cli_agent_messages", None)
    if not messages:
        return
    try:
        role, _prev = messages[-1]
    except (TypeError, ValueError, IndexError):
        return
    if role != "assistant":
        return
    messages[-1] = ("assistant", text)


__all__ = [
    "DispatchablePendingOffer",
    "PendingInvestigationOffer",
    "PendingScheduleOffer",
    "arm_pending_investigation_offer",
    "assistant_offers_full_investigation",
    "clear_competing_pending_offers",
    "consume_confirmed_pending_offer",
    "ensure_canonical_investigation_closer",
    "finalize_gather_investigation_offer",
    "first_pending_offer",
    "is_pending_offer_confirmation",
    "is_schedule_only_want_me_to",
    "parse_investigation_accept_message",
    "synthesize_investigation_alert_text",
]
