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


def execute_propose_scheduled_delivery_tool(
    args: dict[str, Any], ctx: ActionToolContext
) -> dict[str, Any]:
    kind = str(args.get("kind", "")).strip().lower()
    cron = " ".join(str(args.get("cron", "")).split())
    timezone = str(args.get("timezone", "UTC")).strip() or "UTC"
    provider = str(args.get("provider", "")).strip().lower()
    chat_id = str(args.get("chat_id", "")).strip()

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
    if provider != Provider.SLACK.value and not chat_id:
        return {
            "ok": False,
            "error": f"--chat-id is required for provider {provider}",
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
    return {
        "ok": True,
        "pending": True,
        "want_me_to": body,
        "closer": f"**Want me to:** {body}?",
        "slash_preview": offer.to_slash_command(),
        "instruction": (
            "End your reply with the closer field exactly. Do NOT call /cron yet — "
            "wait for the user to confirm. Their yes expands to slash_preview."
        ),
    }


def run_propose_scheduled_delivery(
    *,
    kind: str,
    cron: str,
    provider: str,
    timezone: str = "UTC",
    chat_id: str = "",
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {
            "kind": kind,
            "cron": cron,
            "timezone": timezone,
            "provider": provider,
            "chat_id": chat_id,
        },
        context,
        execute_propose_scheduled_delivery_tool,
    )


propose_scheduled_delivery_tool = RegisteredTool(
    name="propose_scheduled_delivery",
    description=(
        "After delivering a recurring briefing (e.g. morning report), record a "
        "structured schedule offer and return the canonical Want me to: closer. "
        "Call this instead of free-texting a schedule question. Do NOT call "
        "/cron add until the user confirms — their yes becomes the slash_preview."
    ),
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
