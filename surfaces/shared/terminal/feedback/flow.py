"""The flow that prompts for feedback and calls every other module.

Shown after every investigation when stdin/stdout is a TTY.
Silently skipped when: not a TTY, the user has opted out via prefs, or any
exception occurs — feedback must never disrupt the CLI.
"""

from __future__ import annotations

import contextlib
import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from surfaces.shared.terminal.components.key_reader import restore_stdin_terminal
from surfaces.shared.terminal.feedback.analytics import _emit_analytics
from surfaces.shared.terminal.feedback.context_display import _print_context
from surfaces.shared.terminal.feedback.miss_classification import _classify_miss
from surfaces.shared.terminal.feedback.persistence import (
    _NEVER_AGAIN_KEY,
    _feedback_path,
    _is_disabled,
    _prefs_path,
    _set_disabled,
    _store,
)
from surfaces.shared.terminal.feedback.prompts import (
    _DIM,
    _RESET,
    _pick_rating,
    _read_note,
    _write_raw,
)

if TYPE_CHECKING:
    from rich.console import Console


def _collect(final_state: dict[str, Any], *, console: Console | None) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if _is_disabled():
        return

    _print_context(final_state, console=console)

    from infrastructure.terminal.theme import BRAND, DIM, GLYPH_SUCCESS, PROMPT_ACCENT_ANSI

    if console is not None:
        console.print(
            f"\n[{BRAND}]Was this RCA accurate?[/] [{DIM}]↑↓ · Enter · Esc or s to skip[/]"
        )
    else:
        _write_raw(
            f"\n{PROMPT_ACCENT_ANSI}Was this RCA accurate?{_RESET}"
            f"  {_DIM}↑↓ · Enter · Esc or s to skip{_RESET}\n\n"
        )

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
            _write_raw(f"\n{_DIM}{msg}{_RESET}\n")
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

    # Closed-loop learning: classify partial/inaccurate outcomes so they can be
    # tracked over time and replayed as benchmark regressions.
    miss_record: dict[str, Any] | None = None
    if rating in ("partial", "inaccurate"):
        miss_record = _classify_miss(record, final_state=final_state, console=console)

    if console is not None:
        console.print(f"[{BRAND}]{GLYPH_SUCCESS} Feedback saved.[/] [{DIM}]{_feedback_path()}[/]")
        if miss_record is not None:
            from core.domain.feedback import misses_path

            console.print(f"[{DIM}]  Miss recorded → {misses_path()}[/]")
    else:
        message = f"\n{PROMPT_ACCENT_ANSI}{GLYPH_SUCCESS} Feedback saved.{_RESET}  {_DIM}{_feedback_path()}{_RESET}\n"
        if miss_record is not None:
            from core.domain.feedback import misses_path

            message += f"  {_DIM}Miss recorded → {misses_path()}{_RESET}\n"
        _write_raw(f"{message}\n")


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
