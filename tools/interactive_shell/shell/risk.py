"""Coarse risk classification for a mutating shell command.

Turns a command that already needs approval into a ``(risk, why)`` pair so the
confirmation card can show a human-readable impact and offer an "always allow
commands like this" option. Read-only commands never reach here — they run
without approval — so every classified command changes state; the only question
is how reversible that change is.

The classifier is deliberately conservative: an unrecognized mutation is
``MEDIUM``, and anything matching a destructive or remote pattern is ``HIGH``.
It never reports ``LOW`` for a command it does not positively recognize as a
small, local, reversible write.
"""

from __future__ import annotations

import re
from enum import StrEnum

_OPERATOR_SPLIT = re.compile(r"\|\||&&|[|;\n]|>>|>|<|&")


class CommandRisk(StrEnum):
    """How much a mutating command can hurt if approved."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# First-token verbs that destroy data or cannot be undone.
_DESTRUCTIVE_VERBS = frozenset(
    {"rm", "rmdir", "shred", "dd", "mkfs", "fdisk", "truncate", "unlink", "srm"}
)
# Substrings that mark a remote, irreversible, or destructive action regardless
# of the leading verb (checked against the whole command, lowercased).
_HIGH_RISK_PATTERNS: tuple[str, ...] = (
    "git push",
    "git reset --hard",
    "git clean",
    "git rebase",
    "kubectl delete",
    "docker rm",
    "docker rmi",
    "drop table",
    "drop database",
    "> /dev/",
    "chmod -r",
    "chown -r",
    "sudo ",
)
# Commands that reach the network / a remote system.
_NETWORK_VERBS = frozenset(
    {"curl", "wget", "ssh", "scp", "rsync", "kubectl", "helm", "aws", "gcloud", "terraform"}
)
# Verbs that change installed software.
_PACKAGE_VERBS = frozenset({"pip", "pip3", "uv", "npm", "yarn", "pnpm", "brew", "apt", "cargo"})
# Small, local, reversible writes.
_CREATE_VERBS = frozenset({"mkdir", "touch"})
_EDIT_VERBS = frozenset({"cp", "mv", "ln", "sed", "tee"})


def _first_verb(segment: str) -> str:
    """The executable name of a command segment, ignoring env-var prefixes."""
    for token in segment.strip().split():
        if "=" in token and not token.startswith("-"):
            continue  # skip ENV=val prefixes
        return token.rsplit("/", 1)[-1]
    return ""


def _segments(command: str) -> list[str]:
    return [seg for seg in _OPERATOR_SPLIT.split(command) if seg.strip()]


def classify_command_risk(command: str) -> tuple[CommandRisk, str]:
    """Return the risk level and a one-line human impact for ``command``.

    ``command`` is the full shell string the user is being asked to approve; a
    leading ``$ `` display prompt is stripped so it is not read as the verb.
    """
    command = command.strip()
    if command.startswith("$"):
        command = command[1:].lstrip()
    lowered = command.lower()
    if any(pattern in lowered for pattern in _HIGH_RISK_PATTERNS):
        return CommandRisk.HIGH, "Destructive or remote action that may be irreversible."

    verbs = [_first_verb(seg) for seg in _segments(command)]
    verbs = [verb for verb in verbs if verb]

    if any(verb in _DESTRUCTIVE_VERBS for verb in verbs):
        return CommandRisk.HIGH, "Deletes or overwrites data; likely irreversible."
    if any(verb in _NETWORK_VERBS for verb in verbs):
        return CommandRisk.HIGH, "Contacts a remote system or network resource."
    if any(verb in _PACKAGE_VERBS for verb in verbs):
        return CommandRisk.MEDIUM, "Changes installed packages or the environment."

    # A single ``>`` can clobber an existing file, and in-place edits change
    # existing data — reversible in principle, but not a no-op.
    overwrites = re.search(r"(?<!>)>(?!>)", command) is not None
    if overwrites or any(verb in _EDIT_VERBS for verb in verbs):
        return CommandRisk.MEDIUM, "Creates, overwrites, or edits local files."
    if ">>" in command or any(verb in _CREATE_VERBS for verb in verbs):
        return CommandRisk.LOW, "Creates new files or appends to them; reversible."

    return CommandRisk.MEDIUM, "Changes local state."


__all__ = ["CommandRisk", "classify_command_risk"]
