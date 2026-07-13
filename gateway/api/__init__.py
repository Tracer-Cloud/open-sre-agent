"""Authenticated HTTP API under ``/api/*`` (Clerk JWT)."""

from __future__ import annotations

from gateway.api.investigations import router as investigations_router

__all__ = ["investigations_router"]
