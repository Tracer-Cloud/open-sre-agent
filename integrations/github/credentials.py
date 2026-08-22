"""GitHub credential extraction and runtime injection constants."""

from __future__ import annotations

from typing import Any

# Runtime connection/secret kwargs from ``extract_params``; must win over model input.
GITHUB_INJECTED_PARAMS: tuple[str, ...] = (
    "github_url",
    "github_mode",
    "github_token",
    "github_command",
    "github_args",
)


def github_creds(gh: dict) -> dict[str, Any]:
    """Map classified GitHub integration fields to tool credential kwargs."""
    creds: dict[str, Any] = {}
    url = gh.get("github_url") or gh.get("url")
    if url:
        creds["github_url"] = url
    mode = gh.get("github_mode") or gh.get("mode")
    if mode:
        creds["github_mode"] = mode
    token = gh.get("github_token") or gh.get("auth_token")
    if token:
        creds["github_token"] = token
    command = gh.get("github_command") or gh.get("command")
    if command:
        creds["github_command"] = command
    args = gh.get("github_args")
    if args is None:
        args = gh.get("args")
    if args:
        creds["github_args"] = list(args)
    return creds
