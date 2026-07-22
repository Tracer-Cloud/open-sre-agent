"""GitLab environment variable names."""

from __future__ import annotations

GITLAB_BASE_URL_ENV = "GITLAB_BASE_URL"
# Mirrors the ``auth_token`` credential; the names deliberately differ.
GITLAB_AUTH_TOKEN_ENV = "GITLAB_ACCESS_TOKEN"

__all__ = [
    "GITLAB_AUTH_TOKEN_ENV",
    "GITLAB_BASE_URL_ENV",
]
