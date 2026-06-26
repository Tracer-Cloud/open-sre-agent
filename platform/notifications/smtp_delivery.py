"""SMTP delivery helper for background RCA email notifications."""

from __future__ import annotations

import logging
import smtplib
from contextlib import suppress
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


def format_background_rca_email(
    *,
    task_id: str,
    command: str,
    root_cause: str,
    top_analysis: tuple[str, ...],
    next_steps: tuple[str, ...],
    stats: dict[str, Any],
) -> tuple[str, str]:
    """Build a basic subject/body pair for RCA completion emails."""
    subject = f"OpenSRE RCA complete: {task_id}"
    lines = [
        "OpenSRE background investigation completed.",
        "",
        f"Task ID: {task_id}",
        f"Command: {command}",
        "",
        "Root cause",
        root_cause or "Unavailable",
        "",
        "Top analysis",
    ]
    if top_analysis:
        lines.extend(f"- {line}" for line in top_analysis)
    else:
        lines.append("- Unavailable")
    lines.extend(["", "What to do next"])
    if next_steps:
        lines.extend(f"- {line}" for line in next_steps)
    else:
        lines.append("- Unavailable")
    lines.extend(
        [
            "",
            "Internal stats",
            f"- tool calls: {int(stats.get('tool_call_count', 0) or 0)}",
            f"- investigation loops: {int(stats.get('investigation_loop_count', 0) or 0)}",
            f"- validity score: {float(stats.get('validity_score', 0.0) or 0.0):.2f}",
        ]
    )
    return subject, "\n".join(lines)


def _connect_client(config: dict[str, Any]) -> smtplib.SMTP:
    host = str(config.get("host") or "").strip()
    port = int(config.get("port") or 587)
    security = str(config.get("security") or "starttls").strip().lower()
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")

    if security == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        client = smtplib.SMTP(host, port, timeout=15)
    try:
        client.ehlo()
        if security == "starttls":
            client.starttls()
            client.ehlo()
        if username and password:
            client.login(username, password)
    except Exception:
        with suppress(Exception):
            client.close()
        raise
    return client


def verify_smtp_connection(config: dict[str, Any]) -> tuple[bool, str]:
    """Validate SMTP connectivity and optional authentication."""
    try:
        client = _connect_client(config)
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP connection failed: {exc}"
    try:
        client.noop()
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP NOOP failed: {exc}"
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            client.close()
    return True, "Connected to SMTP server successfully."


def send_smtp_report(
    *,
    report: str,
    subject: str,
    smtp_ctx: dict[str, Any],
    to_address: str = "",
) -> tuple[bool, str]:
    """Send a plain-text report via SMTP."""
    recipient = to_address.strip() or str(smtp_ctx.get("default_to") or "").strip()
    from_address = str(smtp_ctx.get("from_address") or "").strip()
    if not recipient:
        return False, "Missing recipient email address"
    if not from_address:
        return False, "Missing from_address"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_address
    message["To"] = recipient
    message.set_content(report)

    try:
        client = _connect_client(smtp_ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] connection failed: %s", exc)
        return False, str(exc)
    try:
        client.send_message(message)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[smtp] send failed: %s", exc)
        return False, str(exc)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001
            client.close()
    return True, ""
