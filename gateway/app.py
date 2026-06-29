"""FastAPI application for Telegram webhook ingress."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.config.get_gateway_settings import load_gateway_settings
from gateway.core.runner import GatewayRunner, set_runner
from gateway.routers import health_router, telegram_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    settings = load_gateway_settings()
    runner = GatewayRunner(settings)
    runner.bind_loop(asyncio.get_running_loop())
    ok, error = runner.setup_webhook()
    if not ok:
        logger.error("[telegram-gateway] setWebhook failed: %s", error)
    set_runner(runner)
    app.state.runner = runner
    yield
    runner.clear_webhook()
    runner.shutdown()
    set_runner(None)


app = FastAPI(title="OpenSRE Telegram Gateway", lifespan=_lifespan)
app.include_router(health_router)
app.include_router(telegram_router)
