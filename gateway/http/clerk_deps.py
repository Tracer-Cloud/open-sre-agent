"""FastAPI dependency that verifies Clerk JWTs for ``/api/*`` routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from platform.auth.jwt_auth import JWTClaims, JWTVerificationError, verify_jwt_async

_bearer = HTTPBearer(auto_error=False)


async def verify_clerk_jwt(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> JWTClaims:
    """Require a valid Clerk bearer token; return claims or raise 401."""
    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await verify_jwt_async(credentials.credentials.strip())
    except JWTVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


ClerkClaims = Annotated[JWTClaims, Depends(verify_clerk_jwt)]
