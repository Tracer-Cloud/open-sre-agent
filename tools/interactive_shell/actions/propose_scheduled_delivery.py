"""Record a structured schedule offer awaiting bare yes confirmation."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_offer import PendingScheduleOffer
from core.agent_harness.tools.tool_context import (
    ActionToolContext,
    execute_with_action_context,
    object_schema,
    string_property,
)
from core.tool_framework.registered_tool import RegisteredTool
from platform.scheduler.types import Provider, TaskKind

# Match surfaces.cli.commands.cron: Sentry kinds use `opensre sentry`, not cron add.
_KIND_VALUES = frozenset(
    kind.value
    for kind in TaskKind
    if kind not in (TaskKind.SENTRY_MORNING_DIGEST, TaskKind.SENTRY_UPTIME_WATCH)
)
_PROVIDER_VALUES = frozenset(p.value for p in Provider)

# Morning-report / digest fetches leave these markers on session.history shell rows.
_FETCH_MARKERS = (
    "wttr.in",
    "bbc.co.uk",
    "rss.xml",
    "feeds.",
    "news.google",
    "openweathermap",
)
_BRIEFING_MARKERS = (
    "weather",
    "headline",
    "good morning",
    "briefing",
    "°",
    "humidity",
)


def _history_rows(session: Any, history_start: int = 0) -> list[dict[str, Any]]:
    """Rows this turn appended. Earlier turns are not evidence for this offer."""
    history = getattr(session, "history", None) or []
    return [row for row in history[history_start:] if isinstance(row, dict)]


def _has_fetch_evidence(session: Any, history_start: int = 0) -> bool:
    """True when THIS turn ran a successful shell fetch shaped like weather/news."""
    for item in reversed(_history_rows(session, history_start)[-24:]):
        if item.get("type") != "shell" or not item.get("ok", True):
            continue
        text = str(item.get("text", "")).lower()
        if any(marker in text for marker in _FETCH_MARKERS):
            return True
    return False


def _briefing_looks_real(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    lowered = stripped.lower()
    return any(marker in lowered for marker in _BRIEFING_MARKERS)


def execute_propose_scheduled_delivery_tool(
    args: dict[str, Any], ctx: ActionToolContext
) -> dict[str, Any]:
    kind = str(args.get("kind", "")).strip().lower()
    cron = " ".join(str(args.get("cron", "")).split())
    timezone = str(args.get("timezone", "UTC")).strip() or "UTC"
    provider = str(args.get("provider", "")).strip().lower()
    chat_id = str(args.get("chat_id", "")).strip()
    briefing_text = str(args.get("briefing_text", "") or "").strip()

    if kind not in _KIND_VALUES:
        return {
            "ok": False,
            "error": f"unsupported kind {kind!r}",
            "allowed_kinds": sorted(_KIND_VALUES),
        }
    if provider not in _PROVIDER_VALUES:
        return {
            "ok": False,
            "error": f"unsupported provider {provider!r}",
            "allowed_providers": sorted(_PROVIDER_VALUES),
        }
    if len(cron.split()) != 5:
        return {
            "ok": False,
            "error": "cron must have exactly 5 fields (minute hour day month day_of_week)",
        }
    if provider not in {Provider.SLACK.value, Provider.INTERACTIVE_SHELL.value} and not chat_id:
        return {
            "ok": False,
            "error": f"--chat-id is required for provider {provider}",
        }

    # Refuse the failure mode where "give me a morning report" becomes ONLY a
    # Want-me-to closer: no weather, no headlines, nothing delivered.
    if kind == TaskKind.DAILY_SUMMARY.value:
        if not _has_fetch_evidence(ctx.session, getattr(ctx, "history_start", 0)):
            return {
                "ok": False,
                "error": (
                    "No weather/news fetch ran yet this session. For daily_summary, "
                    "run the morning-report shell_run fetches (wttr.in + headlines) "
                    "first, compose the briefing, optionally deliver it, THEN call "
                    "propose_scheduled_delivery with briefing_text set to that "
                    "composed briefing. Do not offer to schedule work that never ran."
                ),
            }
        if not _briefing_looks_real(briefing_text):
            return {
                "ok": False,
                "error": (
                    "briefing_text is required for daily_summary and must contain the "
                    "composed weather + headlines briefing (not empty, not the closer "
                    "alone). Pass the same text you showed or delivered to the user."
                ),
            }

    offer = PendingScheduleOffer(
        kind=kind,
        cron=cron,
        timezone=timezone,
        provider=provider,
        chat_id=chat_id,
    )
    ctx.session.pending_schedule_offer = offer
    body = offer.want_me_to_body()
    closer = f"**Want me to:** {body}?"
    # Prefer an explicit response_text so the action driver surfaces the
    # briefing even when the model closing message is only the Want-me-to line.
    response_text = closer if not briefing_text else f"{briefing_text}\n\n{closer}"
    return {
        "ok": True,
        "pending": True,
        "want_me_to": body,
        "closer": closer,
        "response_text": response_text,
        "slash_preview": offer.to_slash_command(),
        "instruction": (
            "Show response_text to the user (briefing + closer). End with the "
            "closer exactly. Do NOT call /cron yet — wait for the user to confirm. "
            "Their yes expands to slash_preview."
        ),
    }


def run_propose_scheduled_delivery(
    *,
    kind: str,
    cron: str,
    provider: str,
    timezone: str = "UTC",
    chat_id: str = "",
    briefing_text: str = "",
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {
            "kind": kind,
            "cron": cron,
            "timezone": timezone,
            "provider": provider,
            "chat_id": chat_id,
            "briefing_text": briefing_text,
        },
        context,
        execute_propose_scheduled_delivery_tool,
    )


propose_scheduled_delivery_tool = RegisteredTool(
    name="propose_scheduled_delivery",
    description=(
        "Record a schedule offer the user has NOT yet accepted, and return the "
        "canonical Want me to: closer plus response_text (briefing + closer). "
        "PRECONDITION for daily_summary: weather/news shell_run fetches must "
        "already have succeeded in this session, and briefing_text must be the "
        "composed briefing. This tool schedules nothing and produces no weather "
        "— calling it alone leaves the user with an empty offer. Do NOT call "
        "/cron add until the user confirms; their yes becomes the slash command."
    ),
    use_cases=[
        (
            "A recurring briefing was just composed (and preferably delivered), and "
            "the user has not said whether they want it to repeat"
        ),
        "The user asked outright to schedule something that already ran this turn",
    ],
    anti_examples=[
        (
            "User asks for a report/briefing/digest — produce and deliver it first; "
            "this tool is the LAST step, never the only response"
        ),
        "User already confirmed — use slash_invoke /cron add instead",
        "Nothing has been fetched or composed yet this turn",
    ],
    input_schema=object_schema(
        properties={
            "kind": string_property(
                description=(
                    "Scheduled task kind, e.g. 'daily_summary'. Must be a "
                    f"cron-add kind: {', '.join(sorted(_KIND_VALUES))}."
                ),
                min_length=1,
            ),
            "cron": string_property(
                description="5-field cron expression, e.g. '0 8 * * 1-5'.",
                min_length=9,
            ),
            "timezone": string_property(
                description="IANA timezone (default UTC), e.g. 'Europe/Amsterdam'."
            ),
            "provider": string_property(
                description="Delivery provider: slack, telegram, discord, or rocketchat.",
                min_length=1,
            ),
            "chat_id": string_property(
                description=(
                    "Target chat/channel. Optional for slack (webhook-bound). "
                    "Required for telegram/discord/rocketchat."
                ),
            ),
            "briefing_text": string_property(
                description=(
                    "Required for daily_summary: the composed weather + headlines "
                    "briefing already produced for the user. Returned in "
                    "response_text ahead of the Want me to: closer so the user "
                    "never sees an offer without the report."
                ),
            ),
        },
        required=("kind", "cron", "provider"),
    ),
    source="interactive_shell",
    surfaces=("action",),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_propose_scheduled_delivery,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level="mutating",
)

__all__ = [
    "execute_propose_scheduled_delivery_tool",
    "propose_scheduled_delivery_tool",
    "run_propose_scheduled_delivery",
]
