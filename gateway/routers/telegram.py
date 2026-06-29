"""Telegram webhook ingress route."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from gateway.config.get_gateway_settings import load_gateway_settings
from gateway.core.runner import GatewayRunner, get_runner
from gateway.platforms.telegram.webhook import parse_update

router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    settings = load_gateway_settings()
    if settings.webhook_secret and (
        not x_telegram_bot_api_secret_token
        or not _secrets_match(x_telegram_bot_api_secret_token, settings.webhook_secret)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    event = parse_update(body)
    if event is None:
        return JSONResponse({"ok": True})

    runner: GatewayRunner = getattr(request.app.state, "runner", None) or get_runner()
    asyncio.create_task(runner.handle_inbound(event))
    return JSONResponse({"ok": True})


def _secrets_match(provided: str, expected: str) -> bool:
    return secrets.compare_digest(provided, expected)


async def dispatch_update(update: dict[str, Any]) -> None:
    """Test/helper entrypoint to process one update dict."""
    event = parse_update(update)
    if event is None:
        return
    await get_runner().handle_inbound(event)
