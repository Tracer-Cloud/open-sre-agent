"""The one PII allowlist for every Rootly person record.

Rootly person payloads carry contact detail that must never reach an LLM
context or a chat channel. ``email`` is a **required** field on both the
``User`` and ``UserFlatResponse`` models, and ``UserFlatResponse`` adds
``phone`` and ``slack_id``. On-call reaches people through an included
``users`` record; alerts reach them through ``responders`` and
``notified_users``. One allowlist serves both so there is a single place to
audit.

The allowlist is **structural, not a denylist**: the four keys below are
written out by name and nothing else is copied. A denylist would have to
enumerate every contact field Rootly ships today *and* every one they add next
quarter, and it would fail silently the first time it fell behind.

``platform/masking/`` is deliberately not used here. It is opt-in behind
``OPENSRE_MASK_ENABLED``, so it is off in the default deployment, and its
detector table has an ``EMAIL`` pattern but no phone pattern — it would be both
disabled by default and incomplete when enabled.
"""

from __future__ import annotations

from typing import Any

_UNKNOWN_NAME = "Unknown"


def compose_name(attrs: dict[str, Any]) -> str:
    """Best available display name, never an email address.

    Rootly leaves ``full_name`` null on users who never completed a profile,
    and the obvious fallback — ``email`` — is exactly the field this module
    exists to withhold. First/last are used instead.
    """
    full_name = attrs.get("full_name")
    if full_name:
        return str(full_name)
    first = attrs.get("first_name") or ""
    last = attrs.get("last_name") or ""
    return f"{first} {last}".strip() or _UNKNOWN_NAME


def shape_person(person_id: Any, attrs: dict[str, Any]) -> dict[str, Any]:
    """Shape one person into the four keys that may leave this integration.

    ``id`` is stringified because Rootly is inconsistent about it: an included
    user's ``id`` is a ``str`` while ``Oncall.user_id`` and
    ``UserFlatResponse.id`` are ``int``.
    """
    return {
        "id": "" if person_id is None else str(person_id),
        "name": compose_name(attrs),
        "name_with_team": attrs.get("full_name_with_team", ""),
        "time_zone": attrs.get("time_zone", ""),
    }


def shape_people(entries: Any) -> list[dict[str, Any]]:
    """Apply the allowlist across an embedded list of person objects.

    Alerts embed responders and notified users directly in the alert's
    attributes rather than in a JSON:API ``included`` array, so the id travels
    inside each entry.
    """
    if not isinstance(entries, list):
        return []
    return [shape_person(entry.get("id"), entry) for entry in entries if isinstance(entry, dict)]


__all__ = ["compose_name", "shape_people", "shape_person"]
