"""Per-session persistence: one JSONL file per session under ~/.opensre/sessions/."""

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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_records(session: ReplSession) -> list[dict[str, Any]]:
    started_iso = datetime.fromtimestamp(session.started_at, tz=UTC).isoformat()
    ended_iso = _now_iso()
    duration_secs = max(0, int(datetime.now(UTC).timestamp() - session.started_at))

    records: list[dict[str, Any]] = [
        {
            "type": "session_start",
            "session_id": session.session_id,
            "started_at": started_iso,
            "opensre_version": get_version(),
        }
    ]

    chat_turns = 0
    investigation_turns = 0
    for entry in session.history:
        kind = entry.get("type", "unknown")
        text = str(entry.get("text", ""))[:_MAX_PROMPT_CHARS]
        records.append({"type": "turn", "kind": kind, "text": text})
        if kind == "chat":
            chat_turns += 1
        elif kind in ("alert", "incoming_alert"):
            investigation_turns += 1

    records.append(
        {
            "type": "session_end",
            "ended_at": ended_iso,
            "duration_secs": duration_secs,
            "total_turns": len(session.history),
            "chat_turns": chat_turns,
            "investigation_turns": investigation_turns,
        }
    )
    return records


class SessionStore:
    @staticmethod
    def flush(session: ReplSession) -> None:
        """Write the complete session file. Skips if session has no turns."""
        with contextlib.suppress(Exception):
            if not session.history:
                return
            records = _build_records(session)
            path = _session_path(session.session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def load_recent(n: int = 20) -> list[dict[str, Any]]:
        """Return up to n session summaries, newest first.

        Each entry contains keys from session_start merged with session_end
        (duration_secs, total_turns, chat_turns, investigation_turns).
        Sessions without a session_end record (e.g. crashed) are included
        with those fields set to None.
        """
        sessions_dir = _sessions_dir()
        if not sessions_dir.exists():
            return []

        results: list[dict[str, Any]] = []
        for path in sessions_dir.glob("*.jsonl"):
            with contextlib.suppress(Exception):
                lines = path.read_text(encoding="utf-8").splitlines()
                if not lines:
                    continue

                start_record: dict[str, Any] | None = None
                end_record: dict[str, Any] | None = None

                with contextlib.suppress(json.JSONDecodeError):
                    start_record = json.loads(lines[0])

                with contextlib.suppress(json.JSONDecodeError):
                    last = json.loads(lines[-1])
                    if last.get("type") == "session_end":
                        end_record = last

                if start_record is None or start_record.get("type") != "session_start":
                    continue

                entry: dict[str, Any] = {
                    "session_id": start_record.get("session_id", path.stem),
                    "started_at": start_record.get("started_at"),
                    "opensre_version": start_record.get("opensre_version"),
                    "duration_secs": end_record.get("duration_secs") if end_record else None,
                    "total_turns": end_record.get("total_turns") if end_record else None,
                    "chat_turns": end_record.get("chat_turns") if end_record else None,
                    "investigation_turns": end_record.get("investigation_turns") if end_record else None,
                }
                results.append(entry)

        results.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return results[:n]
