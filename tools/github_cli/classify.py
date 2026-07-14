"""Classify ``gh`` argv as read-only vs mutating."""

from __future__ import annotations

from typing import Literal

GhEffect = Literal["read", "mutate"]

_MUTATE_TOP_LEVEL = frozenset(
    {
        "alias",
        "attestation",
        "cache",
        "extension",
        "gist",
        "label",
        "org",
        "project",
        "release",
        "ruleset",
        "run",
        "secret",
        "ssh-key",
        "variable",
        "workflow",
    }
)

_MUTATE_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "issue": frozenset(
        {
            "create",
            "edit",
            "close",
            "delete",
            "reopen",
            "develop",
            "transfer",
            "lock",
            "unlock",
            "pin",
            "unpin",
            "comment",
            "delete-comment",
            "edit-comment",
        }
    ),
    "pr": frozenset(
        {
            "create",
            "edit",
            "close",
            "merge",
            "ready",
            "reopen",
            "review",
            "comment",
            "checkout",
        }
    ),
    "repo": frozenset(
        {
            "create",
            "delete",
            "edit",
            "archive",
            "unarchive",
            "rename",
            "sync",
            "fork",
            "clone",
            "deploy-key",
        }
    ),
}

_READ_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "issue": frozenset({"list", "view", "status"}),
    "pr": frozenset({"list", "view", "status", "checks", "diff", "files"}),
    "repo": frozenset({"view", "list"}),
    "auth": frozenset({"status"}),
    "search": frozenset({"repos", "issues", "prs", "code", "commits"}),
}

# Global flags that consume a following value (after the ``gh`` binary).
_VALUE_FLAGS = frozenset(
    {
        "-R",
        "--repo",
        "-h",
        "--hostname",
        "--jq",
        "-t",
        "--template",
    }
)


def _positional_tokens(args: list[str]) -> list[str]:
    """Return command positionals, skipping leading global flags."""
    positionals: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if not token or token == "--":
            i += 1
            continue
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if inline:
                i += 1
                continue
            if name in _VALUE_FLAGS and i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
                continue
            i += 1
            continue
        positionals.append(token)
        i += 1
        while i < len(args):
            positionals.append(args[i])
            i += 1
        break
    return positionals


def _api_is_mutate(args: list[str]) -> bool:
    """Return True when ``gh api`` looks like a non-GET request."""
    method = "GET"
    i = 0
    while i < len(args):
        token = args[i]
        if token in {"-X", "--method"} and i + 1 < len(args):
            method = args[i + 1].upper()
            i += 2
            continue
        if token.startswith("-X") and len(token) > 2:
            method = token[2:].upper()
            i += 1
            continue
        if token.startswith("--method="):
            method = token.split("=", 1)[1].upper()
            i += 1
            continue
        if token in {"-F", "--field", "-f", "--raw-field", "--input"}:
            return True
        i += 1
    return method not in {"GET", "HEAD"}


def classify_gh_args(args: list[str] | tuple[str, ...]) -> GhEffect:
    """Classify ``gh`` arguments (without the ``gh`` binary) as read or mutate.

    Fail closed: unknown command shapes are treated as mutate.
    """
    cleaned = [str(a).strip() for a in args if str(a).strip()]
    if not cleaned:
        return "mutate"

    positionals = _positional_tokens(cleaned)
    if not positionals:
        return "mutate"

    command = positionals[0].lower()
    rest = [p for p in positionals[1:] if not p.startswith("-")]

    if command in {"help", "--help", "-h"}:
        return "read"
    if command == "status":
        return "read"
    if command == "api":
        return "mutate" if _api_is_mutate(positionals[1:]) else "read"
    if command in _MUTATE_TOP_LEVEL:
        return "mutate"

    if command in _MUTATE_SUBCOMMANDS or command in _READ_SUBCOMMANDS:
        if not rest:
            return "mutate"
        sub = rest[0].lower()
        if sub in _MUTATE_SUBCOMMANDS.get(command, frozenset()):
            return "mutate"
        if sub in _READ_SUBCOMMANDS.get(command, frozenset()):
            return "read"
        return "mutate"

    return "mutate"


__all__ = ["GhEffect", "classify_gh_args"]
