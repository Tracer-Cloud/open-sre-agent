"""Deterministic read-only classification for shell commands.

A command is read-only only when every executable across its pipeline/sequence
segments is on a curated allowlist and nothing writes to a file. The classifier
is deliberately conservative: anything it cannot prove safe (command
substitution, heredocs, output redirects, unknown executables, command-runners
like ``xargs``/``sudo``, or a read-only tool invoked with a write flag such as
``find -delete`` or ``sort -o``) returns ``False`` so the execution gate still
asks. A read-only command may then run without approval, matching how coding
agents let inspection commands through while still gating mutations.
"""

from __future__ import annotations

import re
import shlex

# Executables that only read state. Command-runners (sudo, env, xargs, timeout,
# nohup, watch), anything that writes (tee, dd, cp, mv, rm), and network tools
# are intentionally absent so they always fall through to the gate.
_READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "dir",
        "vdir",
        "locate",
        "tree",
        "cat",
        "tac",
        "nl",
        "bat",
        "head",
        "tail",
        "less",
        "more",
        "most",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "ack",
        "pwd",
        "echo",
        "printf",
        "wc",
        "stat",
        "file",
        "du",
        "df",
        "which",
        "whereis",
        "whatis",
        "type",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "printenv",
        "date",
        "cal",
        "uname",
        "hostname",
        "arch",
        "id",
        "whoami",
        "groups",
        "who",
        "uptime",
        "uniq",
        "cut",
        "column",
        "comm",
        "join",
        "paste",
        "tr",
        "fold",
        "fmt",
        "expand",
        "unexpand",
        "rev",
        "seq",
        "jq",
        "cksum",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "sha512sum",
        "hexdump",
        "xxd",
        "od",
        "strings",
        "diff",
        "cmp",
        "ps",
        "pstree",
        "pgrep",
        "nproc",
        "lscpu",
        "lsblk",
        "tty",
        "true",
        "false",
    }
)
# git subcommands that are read-only in their common forms. branch/tag/config are
# excluded because they can also mutate (delete, set).
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "ls-files",
        "rev-parse",
        "describe",
        "shortlog",
        "ls-tree",
        "cat-file",
        "for-each-ref",
        "reflog",
    }
)
# find primaries that delete, run, or write instead of only listing.
_FIND_MUTATING_PRIMARIES: frozenset[str] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

_HEREDOC_RE = re.compile(r"<<-?\s*(?:'[^'\n]+'|\"[^\"\n]+\"|[^\s\\|;&<>]+)")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Redirects that do not write a file: stderr/stdout to /dev/null, or fd dups.
_SAFE_REDIRECTS = (
    "2>/dev/null",
    "1>/dev/null",
    "&>/dev/null",
    ">/dev/null",
    "2>&1",
    "1>&2",
)


def _has_file_write_redirect(text: str) -> bool:
    scrubbed = text
    for pattern in _SAFE_REDIRECTS:
        scrubbed = scrubbed.replace(pattern, " ")
    return ">" in scrubbed


def _split_on_operators(text: str) -> list[str] | None:
    """Split into pipeline/sequence segments at unquoted ``| ; & && ||``.

    Returns ``None`` when quoting is unbalanced (cannot classify safely).
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < length:
            current.append(char)
            current.append(text[index + 1])
            index += 2
            continue
        if text[index : index + 2] in ("&&", "||"):
            segments.append("".join(current))
            current = []
            index += 2
            continue
        if char in ("|", ";", "&"):
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quote is not None:
        return None
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _is_redirect_token(token: str) -> bool:
    return (">" in token) or ("<" in token) or token == "/dev/null"


def _executable_is_read_only(exe: str, rest: list[str]) -> bool:
    name = exe.rsplit("/", 1)[-1]
    if name == "git":
        subcommand = next((tok for tok in rest if not tok.startswith("-")), None)
        return subcommand in _GIT_READ_ONLY_SUBCOMMANDS
    if name == "find":
        return not any(tok in _FIND_MUTATING_PRIMARIES for tok in rest)
    if name == "sort":
        return not any(tok.startswith("-o") or tok.startswith("--output") for tok in rest)
    if name in ("yq", "sed", "perl", "awk"):
        # These can edit files in place / run programs; only ever gate them.
        return False
    return name in _READ_ONLY_COMMANDS


def _segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if _ENV_ASSIGN_RE.match(token) or _is_redirect_token(token):
            continue
        return _executable_is_read_only(token, tokens[index + 1 :])
    return False


def is_read_only_shell_command(command: str) -> bool:
    """True when every segment of ``command`` only reads state (see module docs)."""
    text = command.strip()
    if not text:
        return False
    if "$(" in text or "`" in text or _HEREDOC_RE.search(text):
        return False
    if _has_file_write_redirect(text):
        return False
    segments = _split_on_operators(text)
    if not segments:
        return False
    return all(_segment_is_read_only(segment) for segment in segments)


__all__ = ["is_read_only_shell_command"]
