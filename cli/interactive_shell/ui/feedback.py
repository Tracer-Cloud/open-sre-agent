"""Post-investigation accuracy feedback prompt.

Shown after every investigation when stdin/stdout is a TTY.
Silently skipped when: not a TTY, the user has opted out via prefs, or any
exception occurs — feedback must never disrupt the CLI.

Why a custom select menu instead of repl_choose_one() on the CLI path:
  Rich's Live renderer leaves the cursor at an indeterminate row.
  choice_menu._erase_menu_block() assumes a fixed cursor position and can
  redraw in the wrong place after streaming output ends.

  The local :func:`_run_select` erases line-by-line with ``\\x1b[2K`` and is
  robust to any cursor state.  Call :func:`restore_stdin_terminal` before
  entering the menu so investigation progress UI (Ctrl+O watcher) does not
  leave stdin in no-echo mode.  The REPL path keeps :func:`repl_choose_one`
  inside prompt_toolkit's stdout patch context.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.interactive_shell.ui.key_reader import (
    flush_stdin_unix,
    read_key_unix,
    read_key_windows,
    restore_stdin_terminal,
)

if TYPE_CHECKING:
    from rich.console import Console

# Labels mirror the Slack feedback block in utils/slack_delivery.py.
_CHOICES: list[tuple[str, str]] = [
    ("accurate", "Accurate — root cause identified correctly"),
    ("partial", "Partially accurate — missed some issues"),
    ("inaccurate", "Inaccurate — wrong root cause"),
    ("skip", "Skip for now"),
    ("never", "Never ask again"),
]

_NEVER_AGAIN_KEY = "feedback_disabled"
_SKIP_KEYS = (b"s", b"S")

# ANSI helpers (theme colours inlined to avoid import at module level)
_H = "\x1b[1;38;2;185;237;175m"  # HIGHLIGHT bold  (#B9EDAF)
_D = "\x1b[2m"  # dim
_R = "\x1b[0m"  # reset
_HINT = f"  {_D}↑↓ / j k  ·  Enter  ·  Esc / s to skip{_R}"


def _write_raw(text: str) -> None:
    """Write the console-less (CLI/REPL) feedback text in one TTY-safe call.

    Normalises bare ``\\n`` to ``\\r\\n`` when stdout is a TTY so the context,
    header, note and confirmation lines do not staircase under the REPL's
    ``patch_stdout(raw=True)`` proxy, which passes raw-mode output through
    verbatim. ``_run_select`` already emits ``\\r\\n`` for the menu rows; this
    applies the same rule to the surrounding text. For non-TTY stdout
    (piped/captured/tests) the text is written as-is.
    """
    if sys.stdout.isatty():
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    sys.stdout.write(text)
    sys.stdout.flush()


# ── persistence ───────────────────────────────────────────────────────────────


def _config_dir() -> Path:
    from config.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR


def _feedback_path() -> Path:
    return _config_dir() / "feedback.jsonl"


def _prefs_path() -> Path:
    return _config_dir() / "prefs.json"


def _is_disabled() -> bool:
    with contextlib.suppress(Exception):
        path = _prefs_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return bool(data.get(_NEVER_AGAIN_KEY, False))
    return False


def _set_disabled() -> None:
    with contextlib.suppress(Exception):
        path = _prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.exists():
            with contextlib.suppress(Exception):
                data = json.loads(path.read_text(encoding="utf-8"))
        data[_NEVER_AGAIN_KEY] = True
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _store(record: dict[str, Any]) -> None:
    path = _feedback_path()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── analytics ─────────────────────────────────────────────────────────────────


def _emit_analytics(record: dict[str, Any]) -> None:
    from platform.analytics.events import Event
    from platform.analytics.provider import get_analytics

    with contextlib.suppress(Exception):
        props: dict[str, Any] = {
            "feedback_id": record["feedback_id"],
            "rating": record["rating"],
            "has_note": bool(record.get("note")),
            "is_noise": bool(record.get("is_noise", False)),
        }
        for key in ("run_id", "alert_name", "root_cause_category", "investigation_loop_count"):
            if record.get(key):
                props[key] = record[key]
        for key in ("user_id", "user_email", "org_id"):
            if record.get(key):
                props[key] = record[key]
        if record.get("validity_score") is not None:
            props["validity_score"] = str(record["validity_score"])
        get_analytics().capture(Event.INVESTIGATION_FEEDBACK_SUBMITTED, props)


# ── context display ───────────────────────────────────────────────────────────


def _format_root_cause_lines(root: str, *, cols: int) -> list[str]:
    """Wrap root-cause text to terminal width with a hanging ``Root cause:`` prefix."""
    import textwrap

    prefix = "Root cause: "
    content_width = max(20, cols - len(prefix))
    wrapped = textwrap.wrap(root, width=content_width)
    if not wrapped:
        return []
    lines = [prefix + wrapped[0]]
    indent = " " * len(prefix)
    lines.extend(indent + line for line in wrapped[1:])
    return lines


def _root_cause_width(*, console: Console | None) -> int:
    """Best-effort terminal width for root-cause display (matches REPL tables)."""
    import shutil

    from cli.interactive_shell.ui.rendering import _repl_table_width

    if console is not None:
        return _repl_table_width(console)
    return max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)


def _print_context(final_state: dict[str, Any], *, console: Console | None) -> None:
    """Print the root-cause summary above the rating prompt."""
    root = (final_state.get("root_cause") or "").strip()
    if not root:
        return

    cols = _root_cause_width(console=console)

    from rich.markup import escape

    from cli.interactive_shell.ui.theme import BRAND, DIM, SECONDARY

    if console is not None:
        console.print()
        console.rule(characters="─", style=DIM)
        console.print(
            f"[{SECONDARY}]Root cause:[/] [{BRAND}]{escape(root)}[/]",
            soft_wrap=True,
            width=cols,
        )
    else:
        rule = "─" * cols
        body = "\n".join(_format_root_cause_lines(root, cols=cols))
        _write_raw(f"\n{rule}\n{body}\n{rule}\n")


# ── self-contained select (CLI path) ─────────────────────────────────────────


def _run_select(choices: list[tuple[str, str]]) -> str | None:
    """Arrow-key select menu after streaming output.

    Uses per-line ``\\x1b[2K`` (erase line) instead of a block cursor-position
    assumption.  ``restore_stdin_terminal()`` must run before this so the menu
    starts from canonical echo mode rather than the investigation watcher state.

    Returns the selected key string, or None on Esc / Ctrl-C / s.
    """
    restore_stdin_terminal()
    labels = [label for _, label in choices]
    n = len(labels)
    total_lines = n + 1  # n choice lines + 1 hint line
    idx = 0
    is_unix = os.name != "nt"

    if is_unix:
        flush_stdin_unix()

    def _out(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _draw(redraw: bool) -> None:
        if redraw:
            _out(f"\x1b[{total_lines}A")
        for i, label in enumerate(labels):
            if i == idx:
                _out(f"\r\x1b[2K{_H}  > {label}{_R}\r\n")
            else:
                _out(f"\r\x1b[2K{_D}    {label}{_R}\r\n")
        _out(f"\r\x1b[2K{_HINT}\r\n")

    _draw(False)

    while True:
        key = (
            read_key_unix(also_cancel=_SKIP_KEYS)
            if is_unix
            else read_key_windows(also_cancel=_SKIP_KEYS)
        )

        if key == "enter":
            _out(f"\x1b[{total_lines}A\r\x1b[J")
            return choices[idx][0]

        if key in ("cancel", "eof"):
            _out(f"\x1b[{total_lines}A\r\x1b[J")
            return None

        if key == "up":
            idx = (idx - 1) % n
            _draw(True)
        elif key == "down":
            idx = (idx + 1) % n
            _draw(True)


# ── note reader ───────────────────────────────────────────────────────────────


def _read_note(*, console: Console | None) -> str:
    from cli.interactive_shell.ui.theme import DIM, SECONDARY

    restore_stdin_terminal()
    if console is not None:
        console.print(
            f"[{SECONDARY}]What was wrong or missing? [{DIM}](Enter to skip)[/]:[/] ", end=""
        )
    else:
        _write_raw("\nWhat was wrong or missing? (Enter to skip): ")
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        return input().strip()
    return ""


# ── core ──────────────────────────────────────────────────────────────────────


def _pick_rating(*, console: Console | None) -> str | None:
    """Show the rating prompt; returns key or None on cancel/skip."""
    if console is not None:
        from cli.interactive_shell.ui.choice_menu import repl_choose_one, repl_tty_interactive

        if not repl_tty_interactive():
            return None
        return repl_choose_one(title="Was this RCA accurate?", choices=_CHOICES)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return _run_select(_CHOICES)


def _collect(final_state: dict[str, Any], *, console: Console | None) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if _is_disabled():
        return

    _print_context(final_state, console=console)

    from cli.interactive_shell.ui.theme import BRAND, DIM

    if console is not None:
        console.print(
            f"\n[{BRAND}]Was this RCA accurate?[/] [{DIM}]↑↓ · Enter · Esc or s to skip[/]"
        )
    else:
        _write_raw(f"\n{_H}Was this RCA accurate?{_R}  {_D}↑↓ · Enter · Esc or s to skip{_R}\n\n")

    rating = _pick_rating(console=console)
    if not rating or rating == "skip":
        return

    if rating == "never":
        _set_disabled()
        msg = (
            f"Feedback prompts disabled. "
            f"To re-enable, remove {_NEVER_AGAIN_KEY!r} from {_prefs_path()}"
        )
        if console is not None:
            console.print(f"[{DIM}]{msg}[/]")
        else:
            _write_raw(f"\n{_D}{msg}{_R}\n")
        return

    note = ""
    if rating in ("partial", "inaccurate"):
        note = _read_note(console=console)

    record: dict[str, Any] = {
        "feedback_id": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": final_state.get("run_id", ""),
        "alert_name": final_state.get("alert_name", ""),
        "root_cause": (final_state.get("root_cause") or "")[:500],
        "root_cause_category": final_state.get("root_cause_category", ""),
        "validity_score": final_state.get("validity_score"),
        "is_noise": final_state.get("is_noise", False),
        "investigation_loop_count": final_state.get("investigation_loop_count"),
        "user_id": final_state.get("user_id", ""),
        "user_email": final_state.get("user_email", ""),
        "org_id": final_state.get("org_id", ""),
        "rating": rating,
        "note": note,
    }
    _store(record)
    _emit_analytics(record)

    if console is not None:
        console.print(f"[{BRAND}]✓ Feedback saved.[/] [{DIM}]{_feedback_path()}[/]")
    else:
        _write_raw(f"\n{_H}✓ Feedback saved.{_R}  {_D}{_feedback_path()}{_R}\n\n")


def prompt_investigation_feedback(
    final_state: dict[str, Any],
    *,
    console: Console | None = None,
) -> None:
    """Prompt for RCA accuracy feedback; never raises.

    Stores each response to ``~/.opensre/feedback.jsonl`` and emits
    ``investigation_feedback_submitted`` to PostHog with investigation
    provenance (run_id, alert_name, validity_score, root_cause_category, …)
    and user context (user_id, user_email, org_id when available on
    the hosted/JWT path).
    """
    with contextlib.suppress(Exception):
        try:
            _collect(final_state, console=console)
        finally:
            restore_stdin_terminal()
