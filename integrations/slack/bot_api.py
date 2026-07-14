"""Shared Slack Web API helpers for bot-token tools (read / roster / reply).

Transport for agent Slack tools lives here (not in ``tools/slack_*``) so
multiple tools share one client, charset headers, and error hints.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

_API_BASE = "https://slack.com/api"
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_REQUEST_ATTEMPTS = 3
_DEFAULT_RETRY_WAIT_SECONDS = 0.5
_MAX_RETRY_WAIT_SECONDS = 5.0
_PAGE_LIMIT = 200
_MAX_MEMBER_PAGES = 5
_MAX_CHANNEL_LIST_PAGES = 10
# Thread reads: paginate replies to the last page (bounded) for recent replies.
_MAX_THREAD_PAGES = 25
_MAX_TEXT_CHARS_PER_MESSAGE = 2_000

# Slack channel/DM/group IDs look like C0123ABCD — not bare names like "devs".
_CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]{8,}$")


@dataclass(frozen=True)
class SlackBotTarget:
    """Resolved Slack bot credentials.

    ``bot_token`` is excluded from repr so traces/assertions do not leak it.
    """

    bot_token: str

    def __repr__(self) -> str:
        return "SlackBotTarget(bot_token=<redacted>)"


def resolve_bot_token() -> tuple[SlackBotTarget | None, str]:
    """Resolve the Slack bot token from env first, then the integration store."""
    env_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if env_token:
        return SlackBotTarget(bot_token=env_token), ""

    try:
        from integrations.catalog import resolve_effective_integrations

        slack_integration = resolve_effective_integrations().get("slack") or {}
        config = slack_integration.get("config") if isinstance(slack_integration, dict) else {}
        stored_token = str(config.get("bot_token", "") if isinstance(config, dict) else "").strip()
    except Exception as exc:
        return None, str(exc)

    if not stored_token:
        return None, (
            "Slack bot token is not configured. Set SLACK_BOT_TOKEN or configure "
            "the Slack integration."
        )
    return SlackBotTarget(bot_token=stored_token), ""


def _bot_token_from_slack_source(slack: Any) -> str:
    """Read ``bot_token`` from a flat or ``{config: …}`` Slack source dict."""
    if not isinstance(slack, dict):
        return ""
    direct = str(slack.get("bot_token") or "").strip()
    if direct:
        return direct
    nested = slack.get("config")
    if isinstance(nested, dict):
        return str(nested.get("bot_token") or "").strip()
    return ""


def bot_token_configured(sources: dict[str, Any] | None = None) -> bool:
    """True when env, tool ``sources``, or the local store expose a bot token.

    Availability must not depend solely on the turn's resolved-integration map:
    gateway sessions can carry an empty or metadata-only cache (``CONNECTED
    INTEGRATIONS: none``) while ``SLACK_BOT_TOKEN`` / the store still have a
    usable token. ``resolve_bot_token`` already understands that shape.
    """
    env_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if env_token:
        return True
    if _bot_token_from_slack_source((sources or {}).get("slack")):
        return True
    target, _error = resolve_bot_token()
    return target is not None


def _bearer_headers(token: str) -> dict[str, str]:
    # Match delivery.py: Slack wants charset=utf-8 on JSON bodies.
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _api_error_hint(error: str, *, context: str) -> str:
    hints = {
        "not_in_channel": "The bot is not in this channel — /invite it first.",
        "channel_not_found": "No channel with this ID is visible to the bot.",
        "missing_scope": {
            "history": (
                "The Slack app lacks a history scope "
                "(channels:history / groups:history / im:history / mpim:history). "
                "Add it and reinstall the app."
            ),
            "users": ("The Slack app lacks the users:read scope. Add it and reinstall the app."),
            "list": (
                "The Slack app lacks a channel list scope "
                "(channels:read / groups:read / im:read / mpim:read). "
                "Add it and reinstall the app."
            ),
            "post": ("The Slack app lacks chat:write. Add it and reinstall the app."),
            "join": (
                "The Slack app lacks channels:join (public) or needs an invite for private "
                "channels. Add scopes / invite the bot and reinstall if needed."
            ),
            "search": ("The Slack app lacks search:read. Add it and reinstall the app."),
            "reactions": ("The Slack app lacks reactions:write. Add it and reinstall the app."),
        }.get(
            context,
            "The Slack app is missing a required OAuth scope. Reinstall after adding scopes.",
        ),
        "thread_not_found": "No thread with this parent ts was found in the channel.",
    }
    return hints.get(error, f"Slack API error: {error}")


_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """Return a process-wide keep-alive client so calls reuse one connection."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                base_url=_API_BASE,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return _client


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return min(float(raw), _MAX_RETRY_WAIT_SECONDS)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_WAIT_SECONDS


def _request_json(
    method: str,
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Call one Slack Web API method, retrying transient failures.

    Returns ``(payload, "")`` on any HTTP-level success (Slack encodes its own
    errors in ``payload["ok"]`` for callers to classify), or ``(None, detail)``
    with a specific reason — rate-limit, timeout, HTTP status, or bad payload —
    so callers can surface an actionable hint instead of a generic failure.
    """
    client = _shared_client()
    headers = {"Authorization": f"Bearer {token}"}
    if method != "GET":
        headers = _bearer_headers(token)

    last_error = f"Slack {path} request failed."
    for attempt in range(_MAX_REQUEST_ATTEMPTS):
        try:
            if method == "GET":
                response = client.get(f"/{path}", headers=headers, params=params or {})
            else:
                response = client.post(f"/{path}", headers=headers, json=json_body or {})
        except httpx.TimeoutException:
            last_error = f"Slack {path} timed out."
        except httpx.HTTPError:
            last_error = f"Slack {path} request failed."
        else:
            status = response.status_code
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                last_error = f"Slack {path} is rate-limited; try again shortly."
                if attempt < _MAX_REQUEST_ATTEMPTS - 1:
                    time.sleep(_retry_after_seconds(response))
                    continue
                return None, last_error
            if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                last_error = f"Slack {path} returned HTTP {status}."
            elif status >= HTTPStatus.BAD_REQUEST:
                return None, f"Slack {path} returned HTTP {status}."
            else:
                try:
                    payload = response.json()
                except ValueError:
                    return None, f"Slack {path} returned a non-JSON payload."
                if not isinstance(payload, dict):
                    return None, f"Slack {path} returned an unexpected payload."
                return payload, ""
        if attempt < _MAX_REQUEST_ATTEMPTS - 1:
            time.sleep(_DEFAULT_RETRY_WAIT_SECONDS)
    return None, last_error


def normalize_channel_ref(channel: str) -> tuple[bool, str, str]:
    """Return ``(ok, normalized_ref, error)``.

    Accepts Slack IDs (``C…`` / ``D…`` / ``G…``) or ``#channel-name`` / bare names.
    """
    normalized = str(channel or "").strip()
    if not normalized:
        return False, "", "channel cannot be empty."
    if _CHANNEL_ID_RE.match(normalized):
        return True, normalized, ""
    name = normalized[1:] if normalized.startswith("#") else normalized
    name = name.strip()
    if not name or " " in name:
        return (
            False,
            "",
            "channel must be a Slack ID (C…/D…/G…) or a #channel-name.",
        )
    return True, f"#{name}", ""


def resolve_channel_id(target: SlackBotTarget, channel_ref: str) -> tuple[str | None, str]:
    """Resolve ``C…`` or ``#name`` to a channel ID the bot can see."""
    ok, normalized, err = normalize_channel_ref(channel_ref)
    if not ok:
        return None, err
    if not normalized.startswith("#"):
        return normalized, ""

    want = normalized[1:].lower()
    cursor = ""
    for _ in range(_MAX_CHANNEL_LIST_PAGES):
        params: dict[str, Any] = {
            "limit": _PAGE_LIMIT,
            "types": "public_channel,private_channel,mpim,im",
            "exclude_archived": True,
        }
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json(
            "GET", "conversations.list", target.bot_token, params=params
        )
        if payload is None:
            return None, req_err
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="list")

        for raw in payload.get("channels") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").lower()
            if name == want:
                channel_id = str(raw.get("id") or "").strip()
                if channel_id:
                    return channel_id, ""

        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            break

    return None, f"No channel named #{want} is visible to the bot — /invite it or use a C… ID."


def _normalize_message(raw: dict[str, Any]) -> dict[str, str]:
    text = str(raw.get("text") or "")
    if len(text) > _MAX_TEXT_CHARS_PER_MESSAGE:
        text = text[: _MAX_TEXT_CHARS_PER_MESSAGE - 1].rstrip() + "…"
    return {
        "user": str(raw.get("user") or raw.get("bot_id") or "unknown"),
        "ts": str(raw.get("ts") or ""),
        "thread_ts": str(raw.get("thread_ts") or ""),
        "text": text,
    }


def fetch_channel_messages(
    target: SlackBotTarget,
    *,
    channel_id: str,
    limit: int,
    thread_ts: str = "",
) -> tuple[list[dict[str, str]] | None, str]:
    """Fetch the most recent channel messages or thread replies, oldest first."""
    parent = str(thread_ts or "").strip()
    if parent:
        return _fetch_thread_replies(target, channel_id=channel_id, parent=parent, limit=limit)

    payload, req_err = _request_json(
        "GET",
        "conversations.history",
        target.bot_token,
        params={"channel": channel_id, "limit": limit},
    )
    if payload is None:
        return None, req_err
    if not payload.get("ok"):
        return None, _api_error_hint(
            str(payload.get("error") or "unknown_error"), context="history"
        )

    messages = [
        _normalize_message(m) for m in (payload.get("messages") or []) if isinstance(m, dict)
    ]
    messages.reverse()  # history is newest-first; present oldest-first
    return messages, ""


def _fetch_thread_replies(
    target: SlackBotTarget, *, channel_id: str, parent: str, limit: int
) -> tuple[list[dict[str, str]] | None, str]:
    """Return the newest ``limit`` thread replies, oldest-first.

    conversations.replies is oldest-first with no reverse paging, so follow the
    cursor to the true end. If a thread is longer than the safety bound can
    traverse, return an error rather than stale middle replies — never present
    old content as current.
    """
    collected: list[dict[str, str]] = []
    cursor = ""
    for _ in range(_MAX_THREAD_PAGES):
        params: dict[str, Any] = {"channel": channel_id, "ts": parent, "limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json(
            "GET", "conversations.replies", target.bot_token, params=params
        )
        if payload is None:
            return None, req_err
        if not payload.get("ok"):
            return None, _api_error_hint(
                str(payload.get("error") or "unknown_error"), context="history"
            )
        collected.extend(
            _normalize_message(m) for m in (payload.get("messages") or []) if isinstance(m, dict)
        )
        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            return collected[-limit:], ""
    return None, (
        "This thread is too long to read the most recent replies reliably. "
        "Ask about a narrower window or the parent message directly."
    )


def fetch_team_members(
    target: SlackBotTarget,
) -> tuple[list[dict[str, Any]] | None, str, bool]:
    """Fetch workspace members. Returns ``(members, error, truncated)``."""
    members: list[dict[str, Any]] = []
    cursor = ""
    truncated = False
    for page in range(_MAX_MEMBER_PAGES):
        params: dict[str, Any] = {"limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json("GET", "users.list", target.bot_token, params=params)
        if payload is None:
            return None, req_err, False
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="users"), False

        for raw in payload.get("members") or []:
            if not isinstance(raw, dict) or raw.get("deleted"):
                continue
            profile = raw.get("profile") or {}
            username = str(raw.get("name") or "")
            if username == "slackbot":
                continue
            members.append(
                {
                    "id": str(raw.get("id") or ""),
                    "username": username,
                    "real_name": str(profile.get("real_name") or ""),
                    "display_name": str(profile.get("display_name") or ""),
                    "title": str(profile.get("title") or ""),
                    "is_bot": bool(raw.get("is_bot")),
                }
            )

        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            break
        if page == _MAX_MEMBER_PAGES - 1 and cursor:
            truncated = True

    return members, "", truncated


def post_channel_message(
    target: SlackBotTarget,
    *,
    channel_id: str,
    text: str,
    thread_ts: str = "",
) -> tuple[bool, str]:
    """Post plain text to a channel (optionally as a thread reply)."""
    body: dict[str, str] = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    payload, req_err = _request_json("POST", "chat.postMessage", target.bot_token, json_body=body)
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        return False, _api_error_hint(error, context="post")
    return True, ""


def join_channel(target: SlackBotTarget, *, channel_id: str) -> tuple[bool, str]:
    """Join a public channel the bot can see (``conversations.join``)."""
    payload, req_err = _request_json(
        "POST",
        "conversations.join",
        target.bot_token,
        json_body={"channel": channel_id},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error == "already_in_channel":
            return True, ""
        if error == "method_not_supported_for_channel_type":
            return False, (
                "conversations.join only works on public channels. For a private "
                "channel, DM, or group DM, invite the bot instead."
            )
        return False, _api_error_hint(error, context="join")
    return True, ""


def search_messages(
    target: SlackBotTarget,
    *,
    query: str,
    count: int = 20,
) -> tuple[list[dict[str, str]] | None, str]:
    """Search workspace messages (``search.messages``)."""
    q = str(query or "").strip()
    if not q:
        return None, "query cannot be empty."
    limit = max(1, min(int(count), 100))
    payload, req_err = _request_json(
        "GET",
        "search.messages",
        target.bot_token,
        params={"query": q, "count": limit, "sort": "timestamp"},
    )
    if payload is None:
        return None, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        return None, _api_error_hint(error, context="search")

    matches = ((payload.get("messages") or {}).get("matches")) or []
    results: list[dict[str, str]] = []
    for raw in matches:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        if len(text) > _MAX_TEXT_CHARS_PER_MESSAGE:
            text = text[: _MAX_TEXT_CHARS_PER_MESSAGE - 1].rstrip() + "…"
        channel = raw.get("channel") or {}
        channel_id = ""
        if isinstance(channel, dict):
            channel_id = str(channel.get("id") or "")
        results.append(
            {
                "channel_id": channel_id,
                "user": str(raw.get("user") or ""),
                "ts": str(raw.get("ts") or ""),
                "text": text,
                "permalink": str(raw.get("permalink") or ""),
            }
        )
    return results, ""


def add_reaction(
    target: SlackBotTarget,
    *,
    channel_id: str,
    timestamp: str,
    emoji: str,
) -> tuple[bool, str]:
    """Add an emoji reaction to a message (``reactions.add``)."""
    name = str(emoji or "").strip().strip(":")
    if not name:
        return False, "emoji cannot be empty."
    payload, req_err = _request_json(
        "POST",
        "reactions.add",
        target.bot_token,
        json_body={"channel": channel_id, "timestamp": timestamp, "name": name},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error in ("already_reacted", "no_reaction"):
            return True, ""
        return False, _api_error_hint(error, context="reactions")
    return True, ""


def remove_reaction(
    target: SlackBotTarget,
    *,
    channel_id: str,
    timestamp: str,
    emoji: str,
) -> tuple[bool, str]:
    """Remove an emoji reaction from a message (``reactions.remove``)."""
    name = str(emoji or "").strip().strip(":")
    if not name:
        return False, "emoji cannot be empty."
    payload, req_err = _request_json(
        "POST",
        "reactions.remove",
        target.bot_token,
        json_body={"channel": channel_id, "timestamp": timestamp, "name": name},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error in ("no_reaction", "already_reacted"):
            return True, ""
        return False, _api_error_hint(error, context="reactions")
    return True, ""
