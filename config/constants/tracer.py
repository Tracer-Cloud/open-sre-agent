"""Tracer environment variable names."""

from __future__ import annotations

TRACER_BASE_URL_ENV = "TRACER_API_URL"
# Mirrors the ``jwt_token`` credential; the name deliberately differs.
TRACER_JWT_TOKEN_ENV = "JWT_TOKEN"

__all__ = [
    "TRACER_BASE_URL_ENV",
    "TRACER_JWT_TOKEN_ENV",
]
