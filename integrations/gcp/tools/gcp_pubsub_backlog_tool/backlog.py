"""Pub/Sub subscription inventory joined to its backlog metrics.

The backlog is the whole point of asking about Pub/Sub during an incident, and
it does **not** live on the subscription resource. ``pubsub/v1`` returns
configuration only — topic, ack deadline, retry and dead-letter policy — while
the two numbers that say whether consumers are keeping up are Cloud Monitoring
metrics:

``subscription/num_undelivered_messages``
    How deep the backlog is right now.
``subscription/oldest_unacked_message_age``
    How long the oldest un-acked message has been waiting, in seconds. This is
    the one that matters: a large but flat backlog on a batch subscription is
    normal, while a *rising age* means consumers have stopped.

So this module holds the join, keyed on the ``subscription_id`` resource label
that both APIs agree on, and the config normalization either side of it.

Kept separate from the tool entrypoint so shape handling is testable without an
API client.
"""

from __future__ import annotations

from typing import Any

from integrations.gcp.tools.gcp_monitoring_query_tool.series import point_value

#: Metric types read for the backlog join.
UNDELIVERED_METRIC = "pubsub.googleapis.com/subscription/num_undelivered_messages"
OLDEST_AGE_METRIC = "pubsub.googleapis.com/subscription/oldest_unacked_message_age"

#: Age of the oldest un-acked message, in seconds, above which a subscription is
#: worth flagging. Five minutes is long enough not to fire on a normal retry and
#: short enough to catch a consumer that died at the start of an incident.
STALLED_AGE_SECONDS = 300


def subscription_id(name: str) -> str:
    """Return the trailing id of ``projects/p/subscriptions/s``."""
    return name.rsplit("/", 1)[-1] if name else ""


def _sub_object(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``parent[key]`` when it is an object, otherwise an empty one."""
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def _duration_seconds(value: Any) -> float | None:
    """Parse a protobuf duration string such as ``"600s"``."""
    text = str(value or "").strip()
    if not text.endswith("s"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def latest_by_subscription(series_list: list[Any]) -> dict[str, Any]:
    """Map ``subscription_id`` to the most recent point of each series.

    Cloud Monitoring returns points newest-first, so the head of ``points`` is
    the current value.
    """
    latest: dict[str, Any] = {}
    for series in series_list:
        if not isinstance(series, dict):
            continue
        resource = _sub_object(series, "resource")
        labels = resource.get("labels")
        if not isinstance(labels, dict):
            continue
        identifier = str(labels.get("subscription_id", "")).strip()
        if not identifier:
            continue
        points = series.get("points")
        if not isinstance(points, list) or not points:
            continue
        head = points[0]
        if not isinstance(head, dict):
            continue
        value = point_value(head.get("value") or {})
        if value is not None:
            latest[identifier] = value
    return latest


def normalize_subscription(subscription: dict[str, Any], project: str) -> dict[str, Any]:
    """Flatten one subscription's configuration into the agent-readable shape."""
    identifier = subscription_id(str(subscription.get("name", "")))
    dead_letter = _sub_object(subscription, "deadLetterPolicy")
    retry = _sub_object(subscription, "retryPolicy")
    push = _sub_object(subscription, "pushConfig")
    labels = subscription.get("labels")

    normalized: dict[str, Any] = {
        "project": project,
        "name": identifier,
        "topic": subscription_id(str(subscription.get("topic", ""))),
        "ack_deadline_seconds": subscription.get("ackDeadlineSeconds", 0),
        # Push subscriptions fail differently from pull ones: the endpoint 5xxs
        # and Pub/Sub retries, so the backlog grows with no consumer to inspect.
        "delivery": "push" if push.get("pushEndpoint") else "pull",
    }

    endpoint = str(push.get("pushEndpoint", "")).strip()
    if endpoint:
        normalized["push_endpoint"] = endpoint
    if dead_letter:
        normalized["dead_letter_topic"] = subscription_id(
            str(dead_letter.get("deadLetterTopic", ""))
        )
        normalized["max_delivery_attempts"] = dead_letter.get("maxDeliveryAttempts", 0)
    elif retry:
        # No dead-letter topic means a poison message is retried forever, which
        # is a common reason a backlog never drains.
        normalized["dead_letter_topic"] = ""
    minimum = _duration_seconds(retry.get("minimumBackoff"))
    if minimum is not None:
        normalized["retry_min_backoff_seconds"] = minimum
    retention = _duration_seconds(subscription.get("messageRetentionDuration"))
    if retention is not None:
        normalized["message_retention_seconds"] = retention
    if subscription.get("detached"):
        # A detached subscription silently receives nothing at all.
        normalized["detached"] = True
    if isinstance(labels, dict) and labels:
        normalized["labels"] = {str(key): str(value) for key, value in labels.items()}
    return normalized


def attach_backlog(
    subscription: dict[str, Any], undelivered: dict[str, Any], oldest_age: dict[str, Any]
) -> dict[str, Any]:
    """Add the backlog metrics to a normalized subscription, in place."""
    identifier = str(subscription.get("name", ""))
    depth = undelivered.get(identifier)
    age = oldest_age.get(identifier)
    if depth is not None:
        subscription["undelivered_messages"] = depth
    if age is not None:
        subscription["oldest_unacked_age_seconds"] = round(float(age), 1)
        if float(age) >= STALLED_AGE_SECONDS:
            subscription["stalled"] = True
    if depth is None and age is None:
        # Distinguish "no backlog" from "no metric" — an unreadable metric would
        # otherwise render as a healthy zero.
        subscription["backlog_unknown"] = True
    return subscription


def normalize_subscriptions(subscriptions: list[Any], project: str) -> list[dict[str, Any]]:
    """Normalize a listing, skipping anything that is not an object."""
    return [
        normalize_subscription(item, project) for item in subscriptions if isinstance(item, dict)
    ]
