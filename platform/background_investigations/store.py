"""Durable background-investigation records shared across processes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from platform.background_investigations.types import BackgroundInvestigationRecord, coerce_timestamp

logger = logging.getLogger(__name__)

_VERSION = 1
# Matches ``task_registry._MAX_REGISTRY``. This is a history view, not an archive.
_MAX_RECORDS = 100
_LOCK_TIMEOUT_SECONDS = 10.0
# Owner-only: this sits beside integration credentials.
_FILE_MODE = 0o600

_STORE_FILENAME = "investigations.json"
_STORE_DIRNAME = "background"


class BackgroundInvestigationStoreLockTimeout(TimeoutError):
    """Raised when the investigations file cannot be locked in time."""


class UnreadableBackgroundInvestigationsError(RuntimeError):
    """Raised when the investigations file exists but cannot be parsed."""


def _default_path() -> Path:
    """Resolve the store path for the currently bound organization scope."""
    from config.constants.paths import opensre_home

    return opensre_home() / _STORE_DIRNAME / _STORE_FILENAME


class BackgroundInvestigationStore:
    """Background investigation records in one JSON document per principal."""

    def __init__(
        self,
        resolve_path: Callable[[], Path] | Path | None = None,
        *,
        max_records: int = _MAX_RECORDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if resolve_path is None:
            self._resolve_path: Callable[[], Path] = _default_path
        elif callable(resolve_path):
            self._resolve_path = resolve_path
        else:
            self._resolve_path = lambda: Path(resolve_path)
        self._max_records = max_records
        self._clock = clock

    @property
    def path(self) -> Path:
        """Return the document path for the currently bound scope."""
        return self._resolve_path()

    # ── public API ──────────────────────────────────────────────────────────

    def save(self, record: BackgroundInvestigationRecord) -> None:
        """Save ``record`` while preserving creation time and always retaining this write."""
        path = self.path
        now = self._clock()
        with self._locked(path):
            rows = self._load(path)
            others = [row for row in rows if row.get("task_id") != record.task_id]
            existing = next((r for r in rows if r.get("task_id") == record.task_id), None)

            created = record.created_at or 0.0
            if existing is not None:
                created = coerce_timestamp(existing.get("created_at")) or created
            record.created_at = created or now
            record.updated_at = now

            # Keep the current write outside rotation so clock rollback cannot evict it.
            others.sort(key=lambda row: coerce_timestamp(row.get("updated_at")))
            keep = max(self._max_records, 1) - 1
            self._write(path, [*(others[-keep:] if keep else []), record.to_dict()])

    def get(self, task_id: str) -> BackgroundInvestigationRecord | None:
        """Return the stored record for ``task_id``, or ``None``."""
        for row in self._load(self.path):
            if row.get("task_id") == task_id:
                return BackgroundInvestigationRecord.from_dict(row)
        return None

    def list_recent(self, limit: int = 20) -> list[BackgroundInvestigationRecord]:
        """Return at most ``limit`` records newest first; later writes win ties."""
        rows = sorted(
            self._load(self.path),
            key=lambda row: coerce_timestamp(row.get("updated_at")),
        )
        rows.reverse()
        records = [BackgroundInvestigationRecord.from_dict(row) for row in rows]
        return [record for record in records if record is not None][: max(limit, 0)]

    # ── document ────────────────────────────────────────────────────────────

    def _load(self, path: Path) -> list[dict[str, Any]]:
        """Read rows, returning empty when absent and failing closed when damaged."""
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UnreadableBackgroundInvestigationsError(
                f"background investigations file is unreadable: {path}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("records"), list):
            raise UnreadableBackgroundInvestigationsError(
                f"background investigations file has an unexpected shape: {path}"
            )
        return [row for row in data["records"] if isinstance(row, dict)]

    def _write(self, path: Path, rows: list[dict[str, Any]]) -> None:
        payload = {"version": _VERSION, "records": rows}
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            with suppress(OSError):
                Path(temp_name).unlink(missing_ok=True)
        try:
            path.chmod(_FILE_MODE)
        except OSError:
            logger.debug("[background] could not tighten %s", path, exc_info=True)

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(path.with_suffix(".lock")), timeout=_LOCK_TIMEOUT_SECONDS)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise BackgroundInvestigationStoreLockTimeout(
                f"background investigations file is locked: {path}"
            ) from exc


_default_store: BackgroundInvestigationStore | None = None
_default_store_lock = threading.Lock()


def background_investigation_store() -> BackgroundInvestigationStore:
    """Return the process-wide store, resolving its scope on each operation."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = BackgroundInvestigationStore()
        return _default_store


__all__ = [
    "BackgroundInvestigationStore",
    "BackgroundInvestigationStoreLockTimeout",
    "UnreadableBackgroundInvestigationsError",
    "background_investigation_store",
]
