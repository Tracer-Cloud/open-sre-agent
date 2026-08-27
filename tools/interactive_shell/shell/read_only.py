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
        "cd",
        "pushd",
        "popd",
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
# git subcommands that only read. Mutation-capable ones (remote/branch/tag/config)
# are handled separately: read-only only without a write verb or flag.
_GIT_READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "ls-files",
        "rev-parse",
        "rev-list",
        "describe",
        "shortlog",
        "ls-tree",
        "ls-remote",
        "cat-file",
        "for-each-ref",
        "reflog",
        "show-ref",
        "symbolic-ref",
        "name-rev",
        "merge-base",
        "count-objects",
        "whatchanged",
        "verify-commit",
        "verify-tag",
        "grep",
        "version",
        "help",
    }
)
# `git remote <verb>` verbs that change remotes; bare/list/-v/show/get-url read.
_GIT_REMOTE_WRITE_VERBS: frozenset[str] = frozenset(
    {"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune", "update"}
)
# Write flags per subcommand. `-a` reads for branch (all) but writes for tag
# (annotate), so the two sets are kept separate.
_GIT_BRANCH_WRITE_FLAGS: frozenset[str] = frozenset(
    {
        "-d",
        "-D",
        "-m",
        "-M",
        "-c",
        "-C",
        "-u",
        "-f",
        "--delete",
        "--move",
        "--copy",
        "--force",
        "--edit-description",
        "--set-upstream-to",
        "--unset-upstream",
        "--create-reflog",
    }
)
_GIT_TAG_WRITE_FLAGS: frozenset[str] = frozenset(
    {"-a", "-s", "-d", "-f", "-m", "--delete", "--force", "--annotate", "--sign"}
)
# Write flags for `git config`; a bare `key value` pair also writes.
_GIT_CONFIG_WRITE_FLAGS: frozenset[str] = frozenset(
    {
        "--unset",
        "--unset-all",
        "--replace-all",
        "--add",
        "--edit",
        "-e",
        "--rename-section",
        "--remove-section",
    }
)


# Diff-family ``--output`` / ``-o`` write a file. Shared by ``diff``, ``log``,
# ``show``, ``whatchanged``, and other porcelain that reuses the diff options —
# not just ``git diff``.
_GIT_OUTPUT_WRITE_FLAGS: frozenset[str] = frozenset({"--output", "-o"})


def _git_token_is_write_flag(token: str, write_flags: frozenset[str]) -> bool:
    """True when ``token`` is a write flag, including ``--flag=value`` / ``-uupstream``."""
    if token in write_flags:
        return True
    if token.startswith("--") and "=" in token:
        return token.split("=", 1)[0] in write_flags
    # Short option with an attached value (``-uupstream`` for ``-u``).
    return any(
        len(flag) == 2
        and flag.startswith("-")
        and not flag.startswith("--")
        and token.startswith(flag)
        and len(token) > 2
        for flag in write_flags
    )


def _git_is_read_only(rest: list[str]) -> bool:
    positional = [tok for tok in rest if not tok.startswith("-")]
    subcommand = positional[0] if positional else None
    if subcommand is None:
        return True  # bare `git`, `git --version`, `git -h`
    after = rest[rest.index(subcommand) + 1 :] if subcommand in rest else []
    flags_after = [tok for tok in after if tok.startswith("-")]
    if subcommand in _GIT_READ_ONLY_SUBCOMMANDS:
        # ``git {diff,log,show,…} --output=<file>`` overwrites a file — never
        # treat as read-only (fail closed for any allowlisted subcommand).
        return not any(
            _git_token_is_write_flag(flag, _GIT_OUTPUT_WRITE_FLAGS) for flag in flags_after
        )
    positional_after = [tok for tok in after if not tok.startswith("-")]
    if subcommand == "remote":
        return not any(tok in _GIT_REMOTE_WRITE_VERBS for tok in positional_after)
    if subcommand in ("branch", "tag"):
        write_flags = _GIT_BRANCH_WRITE_FLAGS if subcommand == "branch" else _GIT_TAG_WRITE_FLAGS
        # List forms carry only read flags and no positional (create/delete) name.
        # ``--set-upstream-to=<upstream>`` must match even with ``=value`` attached.
        return not positional_after and not any(
            _git_token_is_write_flag(flag, write_flags) for flag in flags_after
        )
    if subcommand == "config":
        if any(_git_token_is_write_flag(flag, _GIT_CONFIG_WRITE_FLAGS) for flag in flags_after):
            return False
        return len(positional_after) <= 1  # `--get key` reads; `key value` writes
    return False


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
        # A newline separates commands like ``;`` — without splitting on it, a
        # mutation on the next line would ride in the first segment and be judged
        # only by the leading read-only executable.
        if char in ("|", ";", "&", "\n"):
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


# git transports that run a helper program (``git ls-remote ext::<cmd>``); these
# execute code regardless of the read-only subcommand, so always gate them.
_DANGEROUS_TRANSPORTS = ("ext::", "fd::")


def _executable_is_read_only(exe: str, rest: list[str]) -> bool:
    name = exe.rsplit("/", 1)[-1]
    if name == "git":
        if any(tok.startswith(_DANGEROUS_TRANSPORTS) for tok in rest):
            return False
        return _git_is_read_only(rest)
    if name == "find":
        return not any(tok in _FIND_MUTATING_PRIMARIES for tok in rest)
    if name == "sort":
        return not any(tok.startswith("-o") or tok.startswith("--output") for tok in rest)
    if name == "date":
        # ``date -s`` / ``date --set=`` changes the system clock.
        return not any(tok in ("-s", "--set") or tok.startswith("--set=") for tok in rest)
    if name == "hostname":
        # A positional argument sets the hostname; only flags read.
        return not any(not tok.startswith("-") for tok in rest)
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
        if _is_redirect_token(token):
            continue
        if _ENV_ASSIGN_RE.match(token):
            # An env-var prefix (LD_PRELOAD, GIT_SSH_COMMAND, GIT_CONFIG_*, …) can
            # change a command's behavior or enable code execution; never auto-run.
            return False
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
