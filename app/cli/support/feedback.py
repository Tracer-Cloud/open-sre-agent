"""Post-investigation accuracy feedback prompt.

Shown after every investigation when stdin/stdout is a TTY.
Silently skipped when: not a TTY, the user has opted out, or any exception
occurs — feedback must never disrupt the CLI.

Why a custom select menu instead of repl_choose_one():
  Rich's Live renderer (used by StreamRenderer) leaves the cursor at an
  indeterminate row.  choice_menu._erase_menu_block() uses \x1b[{N}A to
  move the cursor up by a fixed count, which assumes the cursor is still at
  the bottom of the menu.  After Live ends that assumption breaks, so the
  erase lands at the wrong row and redraws appear frozen.

  The local _run_select() implementation erases line-by-line with \x1b[2K
  (erase entire line, no cursor-position assumption) and is therefore robust
  to any cursor state the streaming renderer leaves behind.  The REPL path
  (console is not None) keeps repl_choose_one() which works correctly inside
  the prompt_toolkit / patch_stdout context.
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

if TYPE_CHECKING:
    from rich.console import Console

# Labels mirror the Slack feedback block in app/utils/slack_delivery.py.
_CHOICES: list[tuple[str, str]] = [
    ("accurate", "Accurate — root cause identified correctly"),
    ("partial", "Partially accurate — missed some issues"),
    ("inaccurate", "Inaccurate — wrong root cause"),
    ("skip", "Skip for now"),
    ("never", "Never ask again"),
]

_NEVER_AGAIN_KEY = "feedback_disabled"

# ANSI helpers (theme colours inlined to avoid import at module level)
_H = "\x1b[1;38;2;185;237;175m"  # HIGHLIGHT bold  (#B9EDAF)
_D = "\x1b[2m"  # dim
_R = "\x1b[0m"  # reset
_HINT = f"  {_D}↑↓ / j k  ·  Space/Enter  ·  Esc to skip{_R}"


# ── persistence ───────────────────────────────────────────────────────────────


def _config_dir() -> Path:
    from app.constants import OPENSRE_HOME_DIR

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
    from app.analytics.events import Event
    from app.analytics.provider import get_analytics

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


def _print_context(final_state: dict[str, Any], *, console: Console | None) -> None:
    """Print a brief root-cause snippet above the rating prompt."""
    root = (final_state.get("root_cause") or "").strip()
    if not root:
        return

    import shutil

    cols = min(88, max(40, shutil.get_terminal_size((80, 24)).columns))
    snippet = root if len(root) <= cols - 14 else root[: cols - 17] + "…"

    from app.cli.interactive_shell.ui.theme import BRAND, DIM, SECONDARY

    if console is not None:
        console.print()
        console.rule(characters="─", style=DIM)
        console.print(f"[{SECONDARY}]Root cause:[/] [{BRAND}]{snippet}[/]")
    else:
        rule = "─" * cols
        sys.stdout.write(f"\n{rule}\nRoot cause: {snippet}\n{rule}\n")
        sys.stdout.flush()


# ── self-contained select (CLI path) ─────────────────────────────────────────


def _flush_stdin_unix() -> None:
    """Discard pending stdin bytes before raw-mode reading."""
    with contextlib.suppress(Exception):
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)  # type: ignore[attr-defined]


def _read_key_unix() -> str:
    """Read one logical keypress in raw mode; returns a normalised action name."""
    import select as _sel
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]
        ch = os.read(fd, 1)
        if not ch:
            return "eof"
        b = ch[0]
        if b in (3, 4):  # Ctrl-C / Ctrl-D
            return "cancel"
        if b in (10, 13, 32):  # LF / CR / Space → select
            return "enter"
        if b == 27:  # ESC or arrow-key prefix
            if _sel.select([fd], [], [], 0.1)[0]:
                nxt = os.read(fd, 1)
                if nxt == b"[" and _sel.select([fd], [], [], 0.1)[0]:
                    arr = os.read(fd, 1)
                    if arr == b"A":
                        return "up"
                    if arr == b"B":
                        return "down"
            return "cancel"
        if ch in (b"j", b"J"):
            return "down"
        if ch in (b"k", b"K"):
            return "up"
        if ch in (b"q", b"Q"):
            return "cancel"
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def _read_key_windows() -> str:
    """Minimal Windows keypress reader."""
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch in (b"\x03", b"\x1b"):
        return "cancel"
    if ch in (b"\r", b" "):
        return "enter"
    if ch == b"\xe0":
        ch2 = msvcrt.getch()  # type: ignore[attr-defined]
        if ch2 == b"H":
            return "up"
        if ch2 == b"P":
            return "down"
    return "ignore"


def _run_select(choices: list[tuple[str, str]]) -> str | None:
    """Arrow-key select menu that works in any TTY context after streaming output.

    Uses per-line \x1b[2K (erase line) instead of a block cursor-position
    assumption, so it redraws correctly regardless of where the streaming
    renderer left the cursor.

    Returns the selected key string, or None on Esc / Ctrl-C.
    """
    labels = [label for _, label in choices]
    n = len(labels)
    total_lines = n + 1  # n choice lines + 1 hint line
    idx = 0
    is_unix = os.name != "nt"

    if is_unix:
        _flush_stdin_unix()

    def _out(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _draw(redraw: bool) -> None:
        if redraw:
            # Move cursor up to start of the menu block
            _out(f"\x1b[{total_lines}A")
        for i, label in enumerate(labels):
            if i == idx:
                _out(f"\r\x1b[2K{_H}  > {label}{_R}\r\n")
            else:
                _out(f"\r\x1b[2K{_D}    {label}{_R}\r\n")
        _out(f"\r\x1b[2K{_HINT}\r\n")

    _draw(False)

    while True:
        key = _read_key_unix() if is_unix else _read_key_windows()

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
        # "ignore" → no redraw


# ── note reader ───────────────────────────────────────────────────────────────


def _read_note(*, console: Console | None) -> str:
    from app.cli.interactive_shell.ui.theme import DIM, SECONDARY

    if console is not None:
        console.print(
            f"[{SECONDARY}]What was wrong or missing? [{DIM}](Enter to skip)[/]:[/] ", end=""
        )
    else:
        sys.stdout.write("\nWhat was wrong or missing? (Enter to skip): ")
        sys.stdout.flush()
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        return input().strip()
    return ""


# ── core ──────────────────────────────────────────────────────────────────────


def _pick_rating(*, console: Console | None) -> str | None:
    """Show the rating select menu; returns key or None on cancel."""
    if console is not None:
        # REPL path: prompt_toolkit / patch_stdout context active.
        # repl_choose_one() works correctly here.
        from app.cli.interactive_shell.ui.choice_menu import repl_choose_one, repl_tty_interactive

        if not repl_tty_interactive():
            return None
        return repl_choose_one(title="Was this RCA accurate?", choices=_CHOICES)

    # CLI path: use the self-contained picker that is robust after streaming.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return _run_select(_CHOICES)


def _collect(final_state: dict[str, Any], *, console: Console | None) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if _is_disabled():
        return

    _print_context(final_state, console=console)

    from app.cli.interactive_shell.ui.theme import BRAND, DIM

    if console is not None:
        console.print(f"\n[{BRAND}]Was this RCA accurate?[/] [{DIM}]↑↓ · Enter · Esc to skip[/]")
    else:
        sys.stdout.write(f"\n{_H}Was this RCA accurate?{_R}  {_D}↑↓ · Enter · Esc to skip{_R}\n\n")
        sys.stdout.flush()

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
            sys.stdout.write(f"\n{_D}{msg}{_R}\n")
            sys.stdout.flush()
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
        sys.stdout.write(f"\n{_H}✓ Feedback saved.{_R}  {_D}{_feedback_path()}{_R}\n\n")
        sys.stdout.flush()


def prompt_investigation_feedback(
    final_state: dict[str, Any],
    *,
    console: Console | None = None,
) -> None:
    """Prompt for RCA accuracy feedback; never raises.

    Stores each response to ``~/.config/opensre/feedback.jsonl`` and emits
    ``investigation_feedback_submitted`` to PostHog with investigation
    provenance (run_id, alert_name, validity_score, root_cause_category, …)
    and user context (user_id, user_email, org_id when available on
    the hosted/JWT path).
    """
    with contextlib.suppress(Exception):
        _collect(final_state, console=console)
