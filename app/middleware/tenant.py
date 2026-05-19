from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.auth.jwt_auth import JWTVerificationError, verify_jwt_async
from app.credentials import _tenant_ctx, set_tenant_context

_EXEMPT_PATHS: frozenset[str] = frozenset({"/", "/health", "/ok"})


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        raw = request.headers.get("Authorization", "")
        token = raw.removeprefix("Bearer ").strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing auth token"})

        try:
            claims = await verify_jwt_async(token)
        except JWTVerificationError:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        token_ctx = set_tenant_context(claims.sub)
        try:
            return await call_next(request)
        finally:
            _tenant_ctx.reset(token_ctx)  # prevents context leak between requests
