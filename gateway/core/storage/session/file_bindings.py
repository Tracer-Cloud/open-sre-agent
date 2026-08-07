"""Session bindings as a JSON file, for laptops and single-writer gateways.

Written the same way as ``integrations.json``: a lock around a read-modify-write
plus an atomic replace, so a reader never sees a partial file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from config.principal import Actor, Principal
from gateway.core.storage.session.records import BindingKey

logger = logging.getLogger(__name__)

# Schema version stamped into the document. A reader that finds a higher number
# than it understands knows the file was written by a newer OpenSRE and can say
# so instead of misreading it. 1 is the only shape that has existed.
_VERSION = 1

# How long to wait for another writer before giving up. Long enough to outlast a
# normal read-modify-write (a few milliseconds), short enough that a wedged
# holder surfaces as an error rather than hanging a Slack turn. Same value the
# integrations store uses for the same reason.
_LOCK_TIMEOUT_SECONDS = 10.0

# Owner-only. The document is not secret, but it sits in the same directory as
# integration credentials, and nothing else needs to read it.
_FILE_MODE = 0o600


class BindingStoreLockTimeout(TimeoutError):
    """Raised when the bindings file cannot be locked in time."""


class UnreadableBindingsError(RuntimeError):
    """Raised when the bindings file exists but cannot be parsed."""


class FileBindingStore:
    """Bindings in one JSON document beside the rest of a principal's context.

    The path is resolved per operation, not at construction: a gateway opens the
    store before any scope is bound, and the organization bound to a turn decides
    which document that turn reads.
    """

    def __init__(
        self,
        resolve_path: Callable[[], Path] | Path,
        *,
        adopter: Callable[[Path], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._resolve_path = (
            resolve_path if callable(resolve_path) else (lambda: Path(resolve_path))
        )
        self._adopter = adopter
        self._adoption_checked: set[str] = set()

    @property
    def path(self) -> Path:
        """The document for the scope bound right now, adopting on first use."""
        path = self._resolve_path()
        self._adopt_once(path)
        return path

    def _adopt_once(self, path: Path) -> None:
        """Carry legacy rows into this document the first time it is reached.

        Checked per resolved path, because the path a worker sees at boot is not
        the one its turns use once an organization is bound.
        """
        key = str(path)
        if self._adopter is None or key in self._adoption_checked:
            return
        self._adoption_checked.add(key)
        if path.is_file():
            return
        rows = self._adopter(path)
        if rows:
            self._insert_missing(path, rows)

    def close(self) -> None:
        """No connection to release; present so backends are interchangeable."""

    # ── document ────────────────────────────────────────────────────────────

    def _load(self, path: Path) -> dict[str, Any]:
        """Read the document. Absent is empty; unreadable is an error.

        An unreadable file must not read as "no bindings": the next write would
        replace a damaged index with a fresh one and lose every conversation
        mapping in it. Transcripts would survive, but nothing would point at
        them again.
        """
        if not path.is_file():
            return {"version": _VERSION, "bindings": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UnreadableBindingsError(f"bindings file is unreadable: {path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("bindings"), list):
            raise UnreadableBindingsError(f"bindings file has an unexpected shape: {path}")
        return data

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        try:
            path.chmod(_FILE_MODE)
        except OSError:
            logger.debug("[gateway] could not tighten %s", path, exc_info=True)

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(path.with_suffix(".lock")), timeout=_LOCK_TIMEOUT_SECONDS)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise BindingStoreLockTimeout(f"bindings file is locked: {path}") from exc

    @staticmethod
    def _matches(row: dict[str, Any], key: BindingKey) -> bool:
        return (
            row.get("platform") == key.platform
            and row.get("chat_id") == key.chat_id
            and row.get("principal_id", "") == key.principal_id
            and row.get("actor_id", "") == key.actor_id
        )

    # ── BindingStore ────────────────────────────────────────────────────────

    def get_session_id(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str | None:
        key = BindingKey.build(platform=platform, chat_id=chat_id, principal=principal, actor=actor)
        for row in self._load(self.path).get("bindings", []):
            if isinstance(row, dict) and self._matches(row, key):
                return str(row.get("session_id", "")) or None
        return None

    def has_any_actor_binding(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
    ) -> bool:
        """Whether any member has a session in this conversation.

        Sessions are per member, so "has the bot joined this thread" is a
        question about the conversation rather than about one member.
        """
        wanted = BindingKey.build(platform=platform, chat_id=chat_id, principal=principal)
        return any(
            isinstance(row, dict)
            and row.get("platform") == wanted.platform
            and row.get("chat_id") == wanted.chat_id
            and row.get("principal_id", "") == wanted.principal_id
            for row in self._load(self.path).get("bindings", [])
        )

    def bind(
        self,
        *,
        platform: str,
        chat_id: str,
        session_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> None:
        key = BindingKey.build(platform=platform, chat_id=chat_id, principal=principal, actor=actor)
        path = self.path
        with self._locked(path):
            data = self._load(path)
            rows = [
                r
                for r in data.get("bindings", [])
                if not (isinstance(r, dict) and self._matches(r, key))
            ]
            rows.append(
                {
                    "platform": key.platform,
                    "chat_id": key.chat_id,
                    "principal_id": key.principal_id,
                    "actor_id": key.actor_id,
                    "session_id": session_id,
                    "updated_at": time.time(),
                }
            )
            self._write(path, {"version": _VERSION, "bindings": rows})

    def rotate(
        self,
        *,
        platform: str,
        chat_id: str,
        principal: Principal | None = None,
        actor: Actor | str | None = None,
    ) -> str:
        """Assign a fresh session id for this conversation and member."""
        new_id = str(uuid.uuid4())
        self.bind(
            platform=platform,
            chat_id=chat_id,
            session_id=new_id,
            principal=principal,
            actor=actor,
        )
        return new_id

    def import_records(self, rows: list[dict[str, Any]]) -> int:
        """Insert rows that have no binding yet. Returns how many were added."""
        return self._insert_missing(self.path, rows)

    def _insert_missing(self, path: Path, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        with self._locked(path):
            data = self._load(path)
            existing = {
                (
                    r.get("platform"),
                    r.get("chat_id"),
                    r.get("principal_id", ""),
                    r.get("actor_id", ""),
                )
                for r in data.get("bindings", [])
                if isinstance(r, dict)
            }
            added = [
                r
                for r in rows
                if (r["platform"], r["chat_id"], r["principal_id"], r["actor_id"]) not in existing
            ]
            if not added:
                return 0
            self._write(path, {"version": _VERSION, "bindings": [*data["bindings"], *added]})
            return len(added)


__all__ = ["BindingStoreLockTimeout", "FileBindingStore", "UnreadableBindingsError"]
