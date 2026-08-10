"""GitHub-specific path validation for owner/repo names and git refs.

Vendor-specific rules for GitHub that cannot use the generic opaque guard:
``.github`` is a real repo name (leading dot), and branch names legitimately
contain ``/``.

GitHub-specific threats:
- ``%2e%2e%2f`` in a ref: GitHub percent-decodes it, so this smuggles ``../``
  through a validator that only looks for literal ``..``.
- ``#`` in a path truncates the path client-side in urllib.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from integrations.github.client import GitHubApiError

# Owner and repo names on GitHub: alphanumeric, -, _, and . (but not leading .)
# except that repo names CAN start with a dot (.github is a real repo).
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._-]*$")

# Git ref forbidden characters from git check-ref-format, plus % which GitHub decodes
_FORBIDDEN_REF_CHARS = frozenset(" ~^:?*[\\\x7f%#")


def safe_owner(owner: str) -> str | None:
    """Return *owner* as a safe GitHub owner name, or ``None`` when invalid."""
    cleaned = (owner or "").strip()
    if not cleaned or len(cleaned) > 39:  # GitHub limit
        return None
    if not _OWNER_RE.fullmatch(cleaned):
        return None
    return quote(cleaned, safe="")


def safe_repo(repo: str) -> str | None:
    """Return *repo* as a safe GitHub repo name, or ``None`` when invalid.

    Allows a leading dot unlike owner names (.github is a real repo).
    """
    cleaned = (repo or "").strip()
    if not cleaned or len(cleaned) > 100:  # GitHub limit
        return None
    if not _REPO_RE.fullmatch(cleaned):
        return None
    return quote(cleaned, safe="")


def safe_git_ref(ref: str) -> str | None:
    """Return *ref* as a safe git reference, or ``None`` when invalid.

    Implements git check-ref-format rules plus GitHub-specific mitigations.
    """
    cleaned = (ref or "").strip()
    if not cleaned:
        return None

    # Apply git check-ref-format rules
    reason = invalid_branch_reason(cleaned)
    if reason is not None:
        return None

    return quote(cleaned, safe="/")


def invalid_branch_reason(branch: str) -> str | None:
    """Return why *branch* is not a usable ref name, or ``None`` when it is.

    Moved in from branches.py and extended with the per-component
    ``check-ref-format`` rules and a ``%`` block (GitHub percent-decodes, so
    ``%2e%2e%2f`` smuggles ``../`` past a literal ``..`` check).
    """
    if not branch:
        return "branch name is empty"
    if any(char in _FORBIDDEN_REF_CHARS or char < " " for char in branch):
        return "branch name contains a character git forbids in a ref or % (GitHub decodes %)"
    if branch.startswith("/") or branch.endswith("/") or "//" in branch:
        return "branch name has an empty path segment"
    if ".." in branch or "@{" in branch:
        return "branch name contains '..' or '@{'"
    if branch.endswith(".") or branch.endswith(".lock") or branch == "@":
        return "branch name ends with '.' or '.lock', or is '@'"

    # Per-component checks (new rules)
    components = branch.split("/")
    for component in components:
        if component.startswith("."):
            return "branch name component starts with '.'"
        if component.endswith(".lock"):
            return "branch name component ends with '.lock'"

    return None


def repo_path(owner: str, repo: str, *segments: str) -> str:
    """Build a safe GitHub API path from owner, repo, and **raw** path segments.

    Every ``segment`` is one path segment and is percent-encoded here, so a
    caller must pass the raw value: forwarding an already-encoded string would
    double-encode it (``feat/foo`` -> ``feat%252Ffoo``). ``/`` and ``%`` are
    therefore rejected rather than encoded, which turns that mistake into a
    loud failure instead of a silent 404. Split a multi-segment endpoint into
    separate arguments.

    Raises GitHubApiError on any rejected component to keep call sites to one line.
    """
    safe_owner_name = safe_owner(owner)
    if safe_owner_name is None:
        raise GitHubApiError(f"Invalid GitHub owner name: {owner}")

    safe_repo_name = safe_repo(repo)
    if safe_repo_name is None:
        raise GitHubApiError(f"Invalid GitHub repository name: {repo}")

    safe_segments = []
    for segment in segments:
        cleaned = (segment or "").strip()
        if not cleaned:
            raise GitHubApiError(f"Empty path segment in: {segments}")
        if "/" in cleaned:
            raise GitHubApiError(f"Path segment must be a single segment: {segment}")
        if any(char in _FORBIDDEN_REF_CHARS or char < " " for char in cleaned):
            raise GitHubApiError(f"Invalid path segment: {segment}")
        if ".." in cleaned:
            raise GitHubApiError(f"Path traversal in segment: {segment}")
        safe_segments.append(quote(cleaned, safe=""))

    return f"/repos/{safe_owner_name}/{safe_repo_name}" + (
        "/" + "/".join(safe_segments) if safe_segments else ""
    )


__all__ = [
    "invalid_branch_reason",
    "repo_path",
    "safe_git_ref",
    "safe_owner",
    "safe_repo",
]
