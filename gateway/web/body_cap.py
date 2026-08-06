"""Body size cap utilities for gateway HTTP routes."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import Request
from fastapi.responses import JSONResponse

MAX_BODY_BYTES = 1 * 1024 * 1024
MAX_ALERT_BODY_BYTES = MAX_BODY_BYTES


async def validate_body_size(request: Request, max_bytes: int = MAX_BODY_BYTES) -> JSONResponse | None:
    try:
        declared_length = int(request.headers.get("content-length", 0))
    except ValueError:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    if declared_length < 0:
        return JSONResponse({"error": "invalid Content-Length"}, status_code=HTTPStatus.BAD_REQUEST)
    if declared_length > max_bytes:
        return JSONResponse(
            {"error": "payload too large"}, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        )

    total = 0
    chunks = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            return JSONResponse(
                {"error": "payload too large"}, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            )
        chunks.append(chunk)
    
    request._body = b"".join(chunks)
    return None
