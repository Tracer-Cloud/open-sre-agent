"""Small GitHub REST client used by GitHub-backed OpenSRE tools."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

JsonPayload = dict[str, Any] | list[Any]


@dataclass(frozen=True)
class GitHubApiError(RuntimeError):
    """Typed GitHub API failure with enough context for callers to report safely."""

    message: str
    status_code: int | None = None
    path: str = ""
    rate_limit_remaining: str | None = None
    rate_limit_reset: str | None = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"GitHub API error {self.status_code}: {self.message}"


def resolve_github_token(github_token: str | None = None) -> str:
    """Resolve a GitHub token from explicit input or standard env vars."""

    return (github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()


def _next_link(headers: Any) -> str | None:
    raw_link = ""
    if hasattr(headers, "get"):
        raw_link = str(headers.get("Link") or headers.get("link") or "")
    for part in raw_link.split(","):
        url_part, _, rel_part = part.partition(";")
        if 'rel="next"' in rel_part or "rel=next" in rel_part:
            return url_part.strip().strip("<>")
    return None


def _decode_json_payload(raw: str, *, path: str) -> JsonPayload:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubApiError("GitHub API returned invalid JSON.", path=path) from exc
    if isinstance(parsed, dict | list):
        return parsed
    return {"value": parsed}


class GitHubRestClient:
    """Minimal GitHub REST API client with pagination and typed errors."""

    def __init__(
        self, github_token: str | None = None, *, base_url: str = "https://api.github.com"
    ) -> None:
        self._token = resolve_github_token(github_token)
        self._base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> JsonPayload:
        if not self._token:
            raise GitHubApiError(
                "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN."
            )

        url = self._url(path, params=params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = request.Request(
            url,
            data=data,
            method=method.upper(),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json; charset=utf-8",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with request.urlopen(req, timeout=20) as response:  # nosemgrep
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = ""
            if exc.fp is not None:
                detail = exc.read().decode("utf-8", errors="replace")
            message = detail or exc.msg or "GitHub API request failed."
            raise GitHubApiError(
                message,
                status_code=exc.code,
                path=path,
                rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining")
                if exc.headers
                else None,
                rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
            ) from exc
        except error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed: {exc.reason}", path=path) from exc

        return _decode_json_payload(raw, path=path)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self._token:
            raise GitHubApiError(
                "GitHub token is required. Configure github_token, GITHUB_TOKEN, or GH_TOKEN."
            )

        url: str | None = self._url(path, params=params)
        items: list[dict[str, Any]] = []
        while url:
            req = request.Request(
                url,
                method="GET",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with request.urlopen(req, timeout=20) as response:  # nosemgrep
                    raw = response.read().decode("utf-8")
                    headers = getattr(response, "headers", {})
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                raise GitHubApiError(
                    detail or exc.msg or "GitHub API request failed.",
                    status_code=exc.code,
                    path=path,
                    rate_limit_remaining=exc.headers.get("X-RateLimit-Remaining")
                    if exc.headers
                    else None,
                    rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
                ) from exc
            except error.URLError as exc:
                raise GitHubApiError(f"GitHub API request failed: {exc.reason}", path=path) from exc

            parsed = _decode_json_payload(raw, path=path) if raw.strip() else []
            if isinstance(parsed, list):
                items.extend(item for item in parsed if isinstance(item, dict))
            elif isinstance(parsed, dict):
                # Search endpoints return objects with an items list.
                raw_items = parsed.get("items")
                if isinstance(raw_items, list):
                    items.extend(item for item in raw_items if isinstance(item, dict))
            url = _next_link(headers)
        return items

    def _url(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            base = path
        else:
            base = f"{self._base_url}/{path.lstrip('/')}"
        query = parse.urlencode(params, doseq=True) if params else ""
        if not query:
            return base
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{query}"


__all__ = ["GitHubApiError", "GitHubRestClient", "JsonPayload", "resolve_github_token"]
