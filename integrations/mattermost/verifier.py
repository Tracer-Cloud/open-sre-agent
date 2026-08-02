"""Mattermost integration verifier — REST ``/api/v4/users/me`` probe."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from integrations.verification import register_verifier, result


def _verify_webhook(source: str, webhook_url: str) -> dict[str, str]:
    """Non-posting reachability probe for an incoming webhook.

    A GET against a valid webhook endpoint never delivers a message; a 404
    means the URL (or its embedded token) is wrong.
    """
    try:
        response = httpx.get(webhook_url, timeout=10, follow_redirects=False)
    except Exception as exc:
        return result("mattermost", source, "failed", f"Mattermost webhook unreachable: {exc}")

    if response.status_code == HTTPStatus.NOT_FOUND:
        return result(
            "mattermost",
            source,
            "failed",
            "Mattermost webhook returned 404; the URL looks invalid.",
        )
    if response.status_code in {
        HTTPStatus.OK,
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.FORBIDDEN,
        HTTPStatus.METHOD_NOT_ALLOWED,
    }:
        return result(
            "mattermost",
            source,
            "passed",
            f"Mattermost webhook endpoint reachable (HTTP {response.status_code}) "
            "using a non-posting probe.",
        )
    return result(
        "mattermost",
        source,
        "failed",
        f"Mattermost webhook probe returned unexpected HTTP {response.status_code}.",
    )


@register_verifier("mattermost")
def verify_mattermost(source: str, config: dict[str, Any]) -> dict[str, str]:
    server_url = str(config.get("server_url", "")).strip().rstrip("/")
    auth_token = str(config.get("auth_token", "")).strip()
    webhook_url = str(config.get("webhook_url", "")).strip()

    if not (auth_token and server_url):
        if webhook_url:
            return _verify_webhook(source, webhook_url)
        if not server_url:
            return result("mattermost", source, "missing", "Missing server_url.")
        return result("mattermost", source, "missing", "Missing auth_token.")

    try:
        response = httpx.get(
            f"{server_url}/api/v4/users/me",
            headers={"Authorization": f"Bearer {auth_token}"},
            timeout=10,
        )
    except Exception as exc:
        return result("mattermost", source, "failed", f"Mattermost API check failed: {exc}")

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return result(
            "mattermost",
            source,
            "failed",
            "Mattermost auth failed: auth_token is invalid or expired.",
        )
    if response.status_code != HTTPStatus.OK:
        return result(
            "mattermost",
            source,
            "failed",
            f"Mattermost API check failed: HTTP {response.status_code}.",
        )

    try:
        payload = response.json()
    except Exception:
        payload = {}
    username = str(payload.get("username", "")).strip()

    # ``config.get(..., "")`` is not enough: a stored-but-unconfigured
    # ``default_channel`` comes through as the key present with value
    # ``None`` (MattermostConfig.default_channel: str | None = None), not the
    # key missing — and ``str(None)`` is the truthy string "None", which would
    # silently defeat this check. ``or ""`` normalizes both "missing" and
    # "present but None" the same way, matching every other read of
    # default_channel in this module (credentials.py, the send-message tool's
    # resolve_target, mattermost_channel.py).
    default_channel = str(config.get("default_channel") or "").strip()
    if not default_channel:
        # Auth is genuinely valid, but every unattended delivery path
        # (investigation reports, watchdog alarms, background-RCA
        # notifications) requires a channel once token credentials are
        # configured — see the "token-first, never a silent webhook
        # fallback" rule in delivery.py/credentials.py. A configured webhook
        # does not rescue this: token credentials without a channel refuse
        # delivery rather than silently using the webhook. Reporting
        # "passed" here would let the integration look healthy while every
        # automatic delivery fails.
        return result(
            "mattermost",
            source,
            "missing",
            f"Connected to Mattermost as @{username or 'unknown'}, but no default_channel "
            "is configured. Investigation reports, watchdog alarms, and background "
            "notifications need one — set MATTERMOST_DEFAULT_CHANNEL or re-run setup. "
            "(mattermost_send_message can still target an explicit channel per call.)",
        )

    return result(
        "mattermost",
        source,
        "passed",
        f"Connected to Mattermost as @{username or 'unknown'}.",
    )
