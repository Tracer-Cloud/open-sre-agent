"""FastAPI routers for the gateway HTTP surface."""

from __future__ import annotations

from gateway.routers.health import router as health_router
from gateway.routers.telegram import router as telegram_router

__all__ = ["health_router", "telegram_router"]
