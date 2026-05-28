"""Per-session persistence: one JSONL file per session under ~/.opensre/sessions/.

Design: incremental writes.
- open_session()  — writes session_start immediately when the REPL starts
- append_turn()   — appends one turn record on every session.record() call
- flush()         — writes session_end on REPL exit or /reset;
                    deletes the file if no turns were recorded (empty session)

This means the current session file always exists on disk while the REPL is
running, so /sessions shows it without any synthetic in-memory tricks, and
future session-switching can read any file uniformly.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.version import get_version

if TYPE_CHECKING:
    from app.cli.interactive_shell.runtime.session import ReplSession

_MAX_PROMPT_CHARS = 200


def _sessions_dir() -> Path:
    from app.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR / "sessions"


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.jsonl"


class SessionStore:
    @staticmethod
    def open_session(session: ReplSession) -> None:
        """Write session_start record, creating the session file on disk.

        Called once at REPL start and again after every /reset (which rotates
        the session_id). Suppresses all I/O errors so the REPL never crashes.
        """
        with contextlib.suppress(Exception):
            path = _session_path(session.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "type": "session_start",
                "session_id": session.session_id,
                "started_at": datetime.fromtimestamp(session.started_at, tz=UTC).isoformat(),
                "opensre_version": get_version(),
            }
            with path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def append_turn(session: ReplSession, kind: str, text: str) -> None:
        """Append one turn record to the open session file.

        Called by ReplSession.record() and record_incoming_alert() on every
        interaction. No-ops silently if the file does not exist (e.g. the
        non-interactive initial_input path that never calls open_session).
        """
        with contextlib.suppress(Exception):
            path = _session_path(session.session_id)
            if not path.exists():
                return
            record = {
                "type": "turn",
                "kind": kind,
                "text": text[:_MAX_PROMPT_CHARS],
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def flush(session: ReplSession) -> None:
        """Write session_end and close the session file.

        Idempotent: no-ops if session_end is already the last line, so
        double-calling (e.g. /reset flow + entrypoint finally) is safe.
        If no turns were recorded the file is deleted instead.
        """
        with contextlib.suppress(Exception):
            path = _session_path(session.session_id)
            if not path.exists():
                return

            lines = path.read_text(encoding="utf-8").splitlines()

            # Idempotency guard — already finalized, nothing to do.
            if lines:
                with contextlib.suppress(json.JSONDecodeError):
                    if json.loads(lines[-1]).get("type") == "session_end":
                        return

            # Count stats from the turn records already on disk
            chat_turns = 0
            investigation_turns = 0
            total_turns = 0
            for line in lines:
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(line)
                    if rec.get("type") != "turn":
                        continue
                    total_turns += 1
                    kind = rec.get("kind", "")
                    if kind == "chat":
                        chat_turns += 1
                    elif kind in ("alert", "incoming_alert"):
                        investigation_turns += 1

            if total_turns == 0:
                # Empty session — nothing useful happened; remove the file.
                path.unlink(missing_ok=True)
                return

            now = datetime.now(UTC)
            started_at = datetime.fromtimestamp(session.started_at, tz=UTC)
            duration_secs = max(0, int((now - started_at).total_seconds()))

            record = {
                "type": "session_end",
                "ended_at": now.isoformat(),
                "duration_secs": duration_secs,
                "total_turns": total_turns,
                "chat_turns": chat_turns,
                "investigation_turns": investigation_turns,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def load_recent(n: int = 20) -> list[dict[str, Any]]:
        """Return up to n session summaries, newest first.

        For completed sessions (have session_end), stats come from that record.
        For in-progress or crashed sessions (no session_end), stats are
        computed by scanning the turn records in the file so /sessions always
        shows accurate counts for the current session.
        """
        sessions_dir = _sessions_dir()
        if not sessions_dir.exists():
            return []

        # Sort by mtime descending so we only read the n most recent files
        # instead of every file in the directory. Guard against files that
        # disappear between the glob and the stat call (concurrent delete).
        def _mtime(p: Path) -> float:
            with contextlib.suppress(OSError):
                return p.stat().st_mtime
            return 0.0

        all_paths = sorted(sessions_dir.glob("*.jsonl"), key=_mtime, reverse=True)

        results: list[dict[str, Any]] = []
        for path in all_paths[: n * 2]:  # 2× buffer for skipped/malformed files
            with contextlib.suppress(Exception):
                lines = path.read_text(encoding="utf-8").splitlines()
                if not lines:
                    continue

                start_record: dict[str, Any] | None = None
                with contextlib.suppress(json.JSONDecodeError):
                    start_record = json.loads(lines[0])

                if start_record is None or start_record.get("type") != "session_start":
                    continue

                # Check if the last line is a session_end
                end_record: dict[str, Any] | None = None
                with contextlib.suppress(json.JSONDecodeError):
                    last = json.loads(lines[-1])
                    if last.get("type") == "session_end":
                        end_record = last

                if end_record is not None:
                    total_turns = end_record.get("total_turns")
                    chat_turns = end_record.get("chat_turns")
                    investigation_turns = end_record.get("investigation_turns")
                    duration_secs = end_record.get("duration_secs")
                else:
                    # In-progress or crashed — count from turn records
                    total_turns = 0
                    chat_turns = 0
                    investigation_turns = 0
                    for line in lines[1:]:
                        with contextlib.suppress(json.JSONDecodeError):
                            rec = json.loads(line)
                            if rec.get("type") != "turn":
                                continue
                            total_turns += 1
                            kind = rec.get("kind", "")
                            if kind == "chat":
                                chat_turns += 1
                            elif kind in ("alert", "incoming_alert"):
                                investigation_turns += 1
                    duration_secs = None

                results.append(
                    {
                        "session_id": start_record.get("session_id", path.stem),
                        "started_at": start_record.get("started_at"),
                        "opensre_version": start_record.get("opensre_version"),
                        "duration_secs": duration_secs,
                        "total_turns": total_turns,
                        "chat_turns": chat_turns,
                        "investigation_turns": investigation_turns,
                        "is_ended": end_record is not None,
                    }
                )

        results.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return results[:n]
