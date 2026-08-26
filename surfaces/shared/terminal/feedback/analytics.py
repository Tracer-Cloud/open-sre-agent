"""Analytics events emitted for investigation feedback."""

from __future__ import annotations

import contextlib
from typing import Any


def _emit_analytics(record: dict[str, Any]) -> None:
    from infrastructure.analytics.events import Event
    from infrastructure.analytics.provider import get_analytics

    with contextlib.suppress(Exception):
        props: dict[str, Any] = {
            "feedback_id": record["feedback_id"],
            "rating": record["rating"],
            "has_note": bool(record.get("note")),
            "is_noise": bool(record.get("is_noise", False)),
        }
        for key in ("run_id", "alert_name", "root_cause_category", "investigation_loop_count"):
            if record.get(key):
                props[key] = record[key]
        for key in ("user_id", "user_email", "org_id"):
            if record.get(key):
                props[key] = record[key]
        if record.get("validity_score") is not None:
            props["validity_score"] = str(record["validity_score"])
        get_analytics().capture(Event.INVESTIGATION_FEEDBACK_SUBMITTED, props)


def _emit_miss_classified(miss_record: dict[str, Any]) -> None:
    """Emit a follow-up event so PostHog dashboards can chart category trends."""
    from infrastructure.analytics.events import Event
    from infrastructure.analytics.provider import get_analytics

    with contextlib.suppress(Exception):
        props: dict[str, Any] = {
            "miss_id": miss_record.get("miss_id", ""),
            "feedback_id": miss_record.get("feedback_id", ""),
            "taxonomy": miss_record.get("taxonomy", ""),
            "rating": miss_record.get("rating", ""),
            "has_detail": bool(miss_record.get("taxonomy_detail")),
        }
        for key in ("run_id", "alert_name", "root_cause_category"):
            if miss_record.get(key):
                props[key] = miss_record[key]
        for key in ("user_id", "org_id"):
            if miss_record.get(key):
                props[key] = miss_record[key]
        get_analytics().capture(Event.INVESTIGATION_MISS_CLASSIFIED, props)
