"""HTTP-probe onboarding integration validators."""

from __future__ import annotations

import httpx

from integrations.config_models import SlackWebhookConfig
from platform.common.url_validation import validate_https_or_loopback_http_url
from platform.notifications.redaction import redact_token

from .shared import IntegrationHealthResult


def validate_slack_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate Slack webhook format and do a non-posting reachability probe."""
    try:
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    try:
        response = httpx.get(
            slack_config.webhook_url,
            timeout=10,
            follow_redirects=False,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Slack webhook validation failed: {err}")

    if response.status_code == 404:
        return IntegrationHealthResult(
            ok=False, detail="Slack webhook returned 404; the URL looks invalid."
        )
    if response.status_code in {200, 400, 403, 405}:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Slack webhook endpoint reachable (HTTP {response.status_code}) using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Slack webhook probe returned unexpected HTTP {response.status_code}.",
    )


def validate_notion_integration(*, api_key: str, database_id: str) -> IntegrationHealthResult:
    """Validate Notion connectivity by querying the target database."""
    try:
        resp = httpx.get(
            f"https://api.notion.com/v1/databases/{database_id}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return IntegrationHealthResult(
                ok=True, detail="Notion database reachable and token valid."
            )
        if resp.status_code == 401:
            return IntegrationHealthResult(ok=False, detail="Notion API key is invalid or expired.")
        if resp.status_code == 404:
            return IntegrationHealthResult(
                ok=False,
                detail="Notion database not found. Check the database ID and sharing settings.",
            )
        return IntegrationHealthResult(
            ok=False, detail=f"Notion returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Notion validation failed: {err}")


def validate_jira_integration(
    *, base_url: str, email: str, api_token: str, project_key: str
) -> IntegrationHealthResult:
    """Validate Jira connectivity and project key accessibility."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/rest/api/3/myself",
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            display = data.get("displayName") or data.get("emailAddress") or email

            project_resp = httpx.get(
                f"{base_url.rstrip('/')}/rest/api/3/project/{project_key}",
                auth=(email, api_token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if project_resp.status_code == 404:
                return IntegrationHealthResult(
                    ok=False, detail=f"Project '{project_key}' not found. Check the project key."
                )
            if project_resp.status_code != 200:
                return IntegrationHealthResult(
                    ok=False,
                    detail=f"Could not verify project '{project_key}': HTTP {project_resp.status_code}.",
                )

            return IntegrationHealthResult(
                ok=True, detail=f"Jira connected as {display}, project '{project_key}' verified."
            )
        if resp.status_code == 401:
            return IntegrationHealthResult(
                ok=False, detail="Jira credentials invalid. Check email and API token."
            )
        if resp.status_code == 404:
            return IntegrationHealthResult(
                ok=False, detail="Jira base URL not found. Check the URL."
            )
        return IntegrationHealthResult(
            ok=False, detail=f"Jira returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Jira validation failed: {err}")


def validate_servicenow_integration(
    *, instance_url: str, username: str, password: str
) -> IntegrationHealthResult:
    """Validate ServiceNow connectivity with a minimal authenticated table read."""
    # Refuse plaintext HTTP to non-loopback hosts before any request is made —
    # the probe sends the password as HTTP Basic auth.
    try:
        base_url = validate_https_or_loopback_http_url(
            instance_url.strip().rstrip("/"),
            service_name="ServiceNow",
            field_name="instance URL",
        )
    except ValueError as err:
        return IntegrationHealthResult(ok=False, detail=str(err))
    try:
        resp = httpx.get(
            f"{base_url}/api/now/table/sys_user",
            params={"sysparm_limit": 1, "sysparm_fields": "user_name"},
            auth=(username, password),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return IntegrationHealthResult(
                ok=True, detail=f"ServiceNow connected as {username} at {base_url}."
            )
        if resp.status_code == 401:
            return IntegrationHealthResult(
                ok=False, detail="ServiceNow credentials invalid. Check username and password."
            )
        if resp.status_code == 403:
            return IntegrationHealthResult(
                ok=False,
                detail=(
                    "ServiceNow authenticated but the user cannot read the sys_user table. "
                    "Grant a role with table read access (e.g. itil)."
                ),
            )
        if resp.status_code == 404:
            return IntegrationHealthResult(
                ok=False, detail="ServiceNow instance URL not found. Check the URL."
            )
        return IntegrationHealthResult(
            ok=False, detail=f"ServiceNow returned unexpected status {resp.status_code}."
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"ServiceNow validation failed: {err}")


def validate_discord_bot(*, bot_token: str) -> IntegrationHealthResult:
    """Validate a Discord bot token by calling the /users/@me endpoint."""
    try:
        resp = httpx.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Discord API unreachable: {err}")

    if resp.status_code == 200:
        username = resp.json().get("username", "unknown")
        return IntegrationHealthResult(ok=True, detail=f"Discord bot authenticated as @{username}.")
    if resp.status_code == 401:
        return IntegrationHealthResult(ok=False, detail="Discord bot token is invalid or revoked.")
    return IntegrationHealthResult(
        ok=False, detail=f"Discord API returned unexpected HTTP {resp.status_code}."
    )


def validate_rocketchat_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate a Rocket.Chat incoming webhook with a non-posting reachability probe."""
    url = webhook_url.strip()
    if not url:
        return IntegrationHealthResult(ok=False, detail="Missing webhook_url.")

    try:
        resp = httpx.get(url, timeout=10, follow_redirects=False)
    except httpx.RequestError as err:
        return IntegrationHealthResult(
            ok=False, detail=f"Rocket.Chat webhook validation failed: {err}"
        )

    if resp.status_code == 404:
        return IntegrationHealthResult(
            ok=False, detail="Rocket.Chat webhook returned 404; the URL looks invalid."
        )
    if resp.status_code in {200, 400, 403, 405}:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Rocket.Chat webhook endpoint reachable (HTTP {resp.status_code}) "
            "using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Rocket.Chat webhook probe returned unexpected HTTP {resp.status_code}.",
    )


def validate_rocketchat(
    *, server_url: str, auth_token: str, user_id: str
) -> IntegrationHealthResult:
    """Validate Rocket.Chat credentials by calling the /api/v1/me endpoint."""
    base = server_url.strip().rstrip("/")
    if not base:
        return IntegrationHealthResult(ok=False, detail="Missing server_url.")
    if not auth_token.strip() or not user_id.strip():
        return IntegrationHealthResult(ok=False, detail="Missing auth_token or user_id.")

    try:
        resp = httpx.get(
            f"{base}/api/v1/me",
            headers={"X-Auth-Token": auth_token, "X-User-Id": user_id},
            timeout=10,
        )
    except httpx.RequestError as err:
        return IntegrationHealthResult(ok=False, detail=f"Rocket.Chat API unreachable: {err}")

    if resp.status_code == 200:
        try:
            username = resp.json().get("username", "unknown")
        except Exception:
            username = "unknown"
        return IntegrationHealthResult(ok=True, detail=f"Rocket.Chat authenticated as @{username}.")
    if resp.status_code == 401:
        return IntegrationHealthResult(
            ok=False, detail="Rocket.Chat auth token or user ID is invalid or expired."
        )
    return IntegrationHealthResult(
        ok=False, detail=f"Rocket.Chat API returned unexpected HTTP {resp.status_code}."
    )


def validate_telegram_bot(*, bot_token: str) -> IntegrationHealthResult:
    """Validate a Telegram bot token by calling the Bot API getMe endpoint."""
    token = bot_token.strip()
    if not token:
        return IntegrationHealthResult(ok=False, detail="Missing bot_token.")

    try:
        resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
    except httpx.RequestError as err:
        # httpx embeds the request URL — which contains the bot token — in
        # transport error messages, so redact before surfacing the detail.
        safe_error = redact_token(str(err), token)
        return IntegrationHealthResult(ok=False, detail=f"Telegram API unreachable: {safe_error}")
    except Exception as err:
        safe_error = redact_token(str(err), token)
        return IntegrationHealthResult(ok=False, detail=f"Telegram API check failed: {safe_error}")

    try:
        payload = resp.json()
    except Exception as err:
        safe_error = redact_token(str(err), token)
        return IntegrationHealthResult(
            ok=False,
            detail=f"Telegram API check failed: HTTP {resp.status_code} ({safe_error}).",
        )

    if not payload.get("ok"):
        description = payload.get("description", "unknown error")
        return IntegrationHealthResult(ok=False, detail=f"Telegram API check failed: {description}")

    user = payload.get("result", {})
    username = str(user.get("username", "")).strip()
    label = f"@{username}" if username else "unknown"
    return IntegrationHealthResult(ok=True, detail=f"Connected to Telegram bot {label}.")
