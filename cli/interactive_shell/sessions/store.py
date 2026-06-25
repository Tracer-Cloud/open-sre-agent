"""Per-session persistence: one JSONL file per session under ~/.opensre/sessions/.

Design: incremental writes.
- open_session()       — writes session_start immediately when the REPL starts
- append_turn()        — appends a turn stub (kind + text) for stats counting
- append_turn_detail() — appends a full turn record (prompt + response) for /resume
- flush()              — writes conversation_snapshot + session_end on exit or /new;
                         deletes the file if no turns were recorded (empty session)
- reopen_session()     — strips trailing session_end so /resume can append to the file
- load_recent()        — returns up to n session summaries for /sessions display
- load_session()       — reads a full session file and extracts conversation for /resume

This means the current session file always exists on disk while the REPL is
running, so /sessions shows it without any synthetic in-memory tricks, and
future session-switching can read any file uniformly.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli.interactive_shell.sessions.protocol import SessionPersistenceSource
from config.version import get_version

_NAME_MAX_CHARS = 50

# Turn kinds that represent user-initiated chat messages.  session.record() is
# called with the route kind, not a normalised "chat" label, so this set must
# cover all routes that produce conversational turns.
_CHAT_KINDS: frozenset[str] = frozenset({"chat", "cli_agent", "cli_help", "follow_up"})


def _sessions_dir() -> Path:
    from config.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR / "sessions"


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.jsonl"


def _derive_name(lines: list[str]) -> str:
    """Derive a human-readable session name from the first substantive turn.

    Prefers turn_detail.prompt (full text) over the turn stub. Falls back
    to the session ID stem if no usable turn exists.
    """
    # Prefer first turn_detail (has full prompt, no truncation)
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if rec.get("type") == "turn_detail" and rec.get("kind") in _CHAT_KINDS | {"alert"}:
                text = (rec.get("prompt") or "").strip().replace("\n", " ")
                if text:
                    return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")
    # Fall back to turn stub text (covers cli_agent/cli_help/follow_up/alert kinds)
    for line in lines[1:]:
        with contextlib.suppress(json.JSONDecodeError):
            rec = json.loads(line)
            if rec.get("type") == "turn" and rec.get("kind") in _CHAT_KINDS | {
                "alert",
                "incoming_alert",
            }:
                text = (rec.get("text") or "").strip().replace("\n", " ")
                if text:
                    return text[:_NAME_MAX_CHARS] + ("…" if len(text) > _NAME_MAX_CHARS else "")
    return ""


class SessionStore:
    @staticmethod
    def open_session(session: SessionPersistenceSource) -> None:
        """Write session_start record, creating the session file on disk.

        Called once at REPL start and again after every /new (which rotates
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
    def _session_is_finalized(path: Path) -> bool:
        if not path.exists():
            return False
        with contextlib.suppress(Exception):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return False
            with contextlib.suppress(json.JSONDecodeError):
                record = json.loads(lines[-1])
                return isinstance(record, dict) and record.get("type") == "session_end"
        return False

    @staticmethod
    def _ensure_session_open(session_id: str) -> None:
        """Reopen a finalized session file so append paths can continue writing."""
        path = _session_path(session_id)
        if SessionStore._session_is_finalized(path):
            SessionStore.reopen_session(session_id)

    @staticmethod
    def append_turn(session: SessionPersistenceSource, kind: str, text: str) -> None:
        """Append a turn stub to the session file for stats counting.

        Called by ReplSession.record() on every interaction. Stubs carry kind
        and the full input text (no truncation). No-ops silently if the file
        does not exist (e.g. the non-interactive initial_input path).
        """
        with contextlib.suppress(Exception):
            path = _session_path(session.session_id)
            if not path.exists():
                return
            SessionStore._ensure_session_open(session.session_id)
            record = {
                "type": "turn",
                "kind": kind,
                "text": text,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def append_turn_detail(
        session_id: str,
        kind: str,
        prompt: str,
        *,
        response: str | None = None,
        turn_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Append a full turn record (prompt + response) for /resume reconstruction.

        Called by PromptRecorder.flush() after each LLM turn completes.
        These records make session files self-contained: /resume can rebuild
        cli_agent_messages from turn_detail records when no conversation_snapshot
        is present (e.g. old files or crash before flush).
        No-ops silently if the session file does not exist.
        """
        with contextlib.suppress(Exception):
            path = _session_path(session_id)
            if not path.exists():
                return
            record: dict[str, Any] = {
                "type": "turn_detail",
                "kind": kind,
                "prompt": prompt,
            }
            if response is not None:
                record["response"] = response
            if turn_id is not None:
                record["turn_id"] = turn_id
            if model is not None:
                record["model"] = model
            if provider is not None:
                record["provider"] = provider
            if latency_ms is not None:
                record["latency_ms"] = latency_ms
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def append_tool_call(
        session_id: str,
        *,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
        source: str | None = None,
    ) -> None:
        """Append one integration/API tool-call result to the session file.

        Written by the conversational data-gathering loop after each tool runs,
        so a session file carries the actual evidence each turn fetched (tool
        name, arguments, and a bounded result snippet) rather than only the
        final prose answer. Callers MUST pass already-redacted, already-truncated
        values: this writer stays a dumb sink and pulls in no agent/tool imports.
        No-ops silently if the session file does not exist.
        """
        with contextlib.suppress(Exception):
            path = _session_path(session_id)
            if not path.exists():
                return
            SessionStore._ensure_session_open(session_id)
            record: dict[str, Any] = {
                "type": "tool_call",
                "ts": datetime.now(UTC).isoformat(),
                "tool": tool,
                "arguments": arguments,
                "ok": ok,
                "result": result,
            }
            if source is not None:
                record["source"] = source
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def flush(session: SessionPersistenceSource) -> None:
        """Write conversation_snapshot + session_end and close the session file.

        Idempotent: no-ops if session_end is already the last line, so
        double-calling (e.g. /new flow + entrypoint finally) is safe.
        If no turns were recorded the file is deleted instead.
        Writes a conversation_snapshot record before session_end so /resume
        can restore cli_agent_messages and accumulated_context exactly.
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

            # Count stats from turn stub records.
            chat_turns = 0
            investigation_turns = 0
            total_turns = 0
            detail_turns = 0
            for line in lines:
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(line)
                    rec_type = rec.get("type")
                    if rec_type == "turn":
                        total_turns += 1
                        kind = rec.get("kind", "")
                        if kind in _CHAT_KINDS:
                            chat_turns += 1
                        elif kind in ("alert", "incoming_alert"):
                            investigation_turns += 1
                    elif rec_type == "turn_detail":
                        detail_turns += 1

            if total_turns == 0 and detail_turns == 0:
                # Empty session — nothing useful happened; remove the file.
                path.unlink(missing_ok=True)
                return

            now = datetime.now(UTC)
            started_at = datetime.fromtimestamp(session.started_at, tz=UTC)
            duration_secs = max(0, int((now - started_at).total_seconds()))

            # Write conversation snapshot so /resume can restore exact LLM context.
            # Isolated suppress: a serialization failure must not prevent session_end.
            with contextlib.suppress(Exception):
                if session.cli_agent_messages or session.accumulated_context:
                    snapshot: dict[str, Any] = {"type": "conversation_snapshot"}
                    if session.cli_agent_messages:
                        snapshot["cli_agent_messages"] = [
                            list(m) for m in session.cli_agent_messages
                        ]
                    if session.accumulated_context:
                        snapshot["accumulated_context"] = dict(session.accumulated_context)
                    with path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

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
    def reopen_session(session_id: str) -> None:
        """Reopen a finalized session file so new turns append to the same file.

        Strips trailing ``conversation_snapshot`` and ``session_end`` records
        written by :meth:`flush`. No-op when the file is missing or still open.
        """
        with contextlib.suppress(Exception):
            path = _session_path(session_id)
            if not path.exists():
                return

            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return

            changed = False
            with contextlib.suppress(json.JSONDecodeError):
                if json.loads(lines[-1]).get("type") == "session_end":
                    lines.pop()
                    changed = True

            if lines:
                with contextlib.suppress(json.JSONDecodeError):
                    if json.loads(lines[-1]).get("type") == "conversation_snapshot":
                        lines.pop()
                        changed = True

            if not changed:
                return

            with path.open("w", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(line + "\n")

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

                end_record: dict[str, Any] | None = None
                has_snapshot = False
                with contextlib.suppress(json.JSONDecodeError):
                    last = json.loads(lines[-1])
                    if last.get("type") == "session_end":
                        end_record = last

                for line in lines:
                    with contextlib.suppress(json.JSONDecodeError):
                        if json.loads(line).get("type") == "conversation_snapshot":
                            has_snapshot = True
                            break

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
                            if kind in _CHAT_KINDS:
                                chat_turns += 1
                            elif kind in ("alert", "incoming_alert"):
                                investigation_turns += 1
                    duration_secs = None

                results.append(
                    {
                        "session_id": start_record.get("session_id", path.stem),
                        "name": _derive_name(lines),
                        "started_at": start_record.get("started_at"),
                        "opensre_version": start_record.get("opensre_version"),
                        "duration_secs": duration_secs,
                        "total_turns": total_turns,
                        "chat_turns": chat_turns,
                        "investigation_turns": investigation_turns,
                        "is_ended": end_record is not None,
                        "has_snapshot": has_snapshot,
                    }
                )

        results.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        return results[:n]

    @staticmethod
    def count_prefix_matches(prefix: str) -> int:
        """Return how many session files whose stem starts with prefix.

        Used by /resume to distinguish 'not found' (0) from 'ambiguous' (>1)
        without re-scanning the directory with a fragile inline import.
        """
        sessions_dir = _sessions_dir()
        if not sessions_dir.exists():
            return 0
        with contextlib.suppress(OSError):
            return sum(1 for p in sessions_dir.glob("*.jsonl") if p.stem.startswith(prefix))
        return 0

    @staticmethod
    def load_session(session_id_prefix: str) -> dict[str, Any] | None:
        """Load a session file and extract conversation data for /resume.

        Accepts a session ID prefix (e.g. the first 8 chars shown by /sessions).
        Returns None if no match found or the prefix is ambiguous.

        Resolution order for cli_agent_messages:
        1. conversation_snapshot (written at clean exit) — exact fidelity
        2. turn_detail records (written per-turn by PromptRecorder) — fallback
           for old files pre-enrichment or sessions that crashed before flush

        Returned dict keys:
          session_id, name, started_at, cli_agent_messages (list[tuple[str,str]]),
          accumulated_context, history (turn stubs), turn_details, has_snapshot
        """
        sessions_dir = _sessions_dir()
        if not sessions_dir.exists():
            return None

        target_path: Path | None = None
        for path in sessions_dir.glob("*.jsonl"):
            if path.stem.startswith(session_id_prefix):
                if target_path is not None:
                    return None  # ambiguous prefix — caller should ask for more chars
                target_path = path

        if target_path is None:
            return None

        with contextlib.suppress(Exception):
            lines = target_path.read_text(encoding="utf-8").splitlines()
            if not lines:
                return None

            start_record: dict[str, Any] | None = None
            with contextlib.suppress(json.JSONDecodeError):
                start_record = json.loads(lines[0])

            if start_record is None or start_record.get("type") != "session_start":
                return None

            cli_agent_messages: list[tuple[str, str]] = []
            accumulated_context: dict[str, Any] = {}
            history: list[dict[str, Any]] = []
            turn_details: list[dict[str, Any]] = []
            has_snapshot = False

            for line in lines[1:]:
                with contextlib.suppress(json.JSONDecodeError):
                    rec = json.loads(line)
                    rec_type = rec.get("type")
                    if rec_type == "turn":
                        history.append(rec)
                    elif rec_type == "turn_detail":
                        turn_details.append(rec)
                    elif rec_type == "conversation_snapshot":
                        has_snapshot = True
                        msgs = rec.get("cli_agent_messages")
                        if msgs:
                            cli_agent_messages = [
                                (str(m[0]), str(m[1])) for m in msgs if len(m) >= 2
                            ]
                        ctx = rec.get("accumulated_context")
                        if ctx and isinstance(ctx, dict):
                            accumulated_context = ctx

            # Fall back to turn_detail reconstruction when no snapshot exists.
            if not cli_agent_messages and turn_details:
                for td in turn_details:
                    if td.get("kind") in ("chat", "follow_up"):
                        prompt = td.get("prompt") or ""
                        response = td.get("response") or ""
                        if prompt:
                            cli_agent_messages.append(("user", prompt))
                        if response:
                            cli_agent_messages.append(("assistant", response))

            return {
                "session_id": start_record.get("session_id", target_path.stem),
                "name": _derive_name(lines),
                "started_at": start_record.get("started_at"),
                "cli_agent_messages": cli_agent_messages,
                "accumulated_context": accumulated_context,
                "history": history,
                "turn_details": turn_details,
                "has_snapshot": has_snapshot,
            }

        return None
