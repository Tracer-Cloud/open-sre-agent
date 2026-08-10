"""How ``open_github_pull_request`` describes itself in an approval prompt.

Approving a pull request the agent composed is a judgement about blast radius:
which repository, which base branch, which files. A generic argument renderer
can only list the fields that were passed, which reads as a form dump — so the
tool renders its own prompt, in the shape a reviewer already knows from GitHub.

This lives in ``integrations/github`` rather than in the gateway on purpose:
knowing that ``changes[].delete`` means a removed file is GitHub knowledge, and
the approval surface must stay vendor-neutral.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# The prompt is read at a glance in a chat message, so every field is bounded.
# The approval surface clamps the whole rendering again as a backstop.
_TITLE_LIMIT = 120
_BRANCH_LIMIT = 80
_PATH_LIMIT = 100
_BODY_LIMIT = 400
_FILE_LIMIT = 12


def _one_line(value: Any, *, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _title(arguments: Mapping[str, Any]) -> str:
    return _one_line(arguments.get("title"), limit=_TITLE_LIMIT) or "(untitled)"


def _repository(arguments: Mapping[str, Any]) -> str:
    owner = _one_line(arguments.get("owner"), limit=_BRANCH_LIMIT)
    repo = _one_line(arguments.get("repo"), limit=_BRANCH_LIMIT)
    slug = "/".join(part for part in (owner, repo) if part)
    return slug or "(repository not set)"


def _body_lines(arguments: Mapping[str, Any]) -> list[str]:
    """The PR description, trimmed — it is the "why" and often the longest field."""
    body = str(arguments.get("body") or "").strip()
    if not body:
        return []
    trimmed = body if len(body) <= _BODY_LIMIT else f"{body[:_BODY_LIMIT]}…"
    return ["", *trimmed.splitlines()]


def _change_line(entry: Any) -> str:
    if not isinstance(entry, Mapping):
        return f"  · {_one_line(entry, limit=_PATH_LIMIT)}"
    path = _one_line(entry.get("path"), limit=_PATH_LIMIT) or "(no path)"
    if entry.get("delete"):
        return f"  · {path} — deleted"
    content = entry.get("content")
    if not isinstance(content, str):
        return f"  · {path} — no contents supplied"
    return f"  · {path} — {len(content):,} chars written"


def _change_lines(raw: Any) -> list[str]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
        return ["", "no files listed"]
    count = len(raw)
    lines = ["", f"{count} file{'' if count == 1 else 's'} changed"]
    lines.extend(_change_line(entry) for entry in raw[:_FILE_LIMIT])
    if count > _FILE_LIMIT:
        lines.append(f"  · … and {count - _FILE_LIMIT} more")
    return lines


class PullRequestApprovalDisplay:
    """Renders the pull-request approval prompt and its outcome."""

    def headline(self, arguments: Mapping[str, Any]) -> str:
        """``Create PR — <title>``, the line the outcome message reuses."""
        return f"Create PR — {_title(arguments)}"

    def details(self, arguments: Mapping[str, Any]) -> str:
        """Repository, both branches, the description, and every file touched."""
        base = _one_line(arguments.get("base_branch"), limit=_BRANCH_LIMIT) or "(not set)"
        head = _one_line(arguments.get("head_branch"), limit=_BRANCH_LIMIT) or "(not set)"
        lines = [_repository(arguments), f"{base}  ←  {head}"]
        if arguments.get("draft"):
            lines.append("draft — required status checks will not run")
        lines.extend(_body_lines(arguments))
        lines.extend(_change_lines(arguments.get("changes")))
        return "\n".join(lines)

    def receipt(self, arguments: Mapping[str, Any], result: Mapping[str, Any]) -> str:
        """``Create PR #123 — <title>  <url>`` once GitHub has answered."""
        number = result.get("pull_request_number") or 0
        url = str(result.get("pull_request_url") or "").strip()
        if not number or not url:
            return ""
        return f"Create PR #{number} — {_title(arguments)}  {url}"


PULL_REQUEST_APPROVAL_DISPLAY = PullRequestApprovalDisplay()

__all__ = ["PULL_REQUEST_APPROVAL_DISPLAY", "PullRequestApprovalDisplay"]
