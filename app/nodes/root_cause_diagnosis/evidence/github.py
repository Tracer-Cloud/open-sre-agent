"""GitHub evidence section builder for root cause diagnosis prompts."""

from __future__ import annotations

import json
from typing import Any


def build_github_section(
    *,
    github_commits: list[Any],
    github_code_matches: list[Any],
    github_file: Any,
) -> str:
    """Build a GitHub evidence section from MCP-backed code data."""
    lines = ["\nGitHub Code Evidence:\n"]

    if github_commits:
        lines.append(f"Recent Commits ({len(github_commits)}):\n")
        for commit in github_commits[:5]:
            if not isinstance(commit, dict):
                lines.append(f"- {str(commit)[:220]}\n")
                continue
            commit_info = commit.get("commit", {}) if isinstance(commit.get("commit"), dict) else {}
            sha = str(commit.get("sha") or commit.get("oid") or commit_info.get("oid") or "")[:12]
            message = str(
                commit.get("message")
                or commit.get("messageHeadline")
                or commit_info.get("message")
                or "unknown"
            )[:220]
            lines.append(f"- {sha or 'unknown'}: {message}\n")

    if github_code_matches:
        lines.append(f"Code Search Matches ({len(github_code_matches)}):\n")
        for match in github_code_matches[:5]:
            if not isinstance(match, dict):
                lines.append(f"- {str(match)[:220]}\n")
                continue
            path = str(
                match.get("path")
                or match.get("file")
                or match.get("name")
                or match.get("filename")
                or "unknown"
            )[:180]
            snippets = match.get("matches") or match.get("fragments") or match.get("lines") or []
            if isinstance(snippets, list) and snippets:
                snippet_text = "; ".join(str(item)[:140] for item in snippets[:2])
            else:
                snippet_text = str(match.get("text", ""))[:220]
            lines.append(f"- {path}: {snippet_text}\n")

    if github_file:
        lines.append("GitHub File Contents:\n")
        if isinstance(github_file, dict):
            path = str(
                github_file.get("path")
                or github_file.get("name")
                or github_file.get("filename")
                or ""
            ).strip()
            if path:
                lines.append(f"- Path: {path}\n")
            content = github_file.get("content")
            if isinstance(content, str) and content:
                lines.append(f"{content[:1000]}\n")
            else:
                lines.append(f"- Details: {json.dumps(github_file, default=str)[:1000]}\n")
        elif isinstance(github_file, list):
            for item in github_file[:5]:
                lines.append(f"- {json.dumps(item, default=str)[:220]}\n")
        else:
            lines.append(f"- {str(github_file)[:1000]}\n")

    return "".join(lines)
