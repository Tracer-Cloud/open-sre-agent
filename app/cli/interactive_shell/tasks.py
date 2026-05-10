"""In-flight task bookkeeping for the interactive shell (REPL tasks + cancellation)."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from subprocess import Popen
from typing import Any

import app.constants as const_module

_TASK_ID_BYTES = 4
_MAX_REGISTRY = 100
_TASKS_STORE_FILENAME = "interactive_tasks.json"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskKind(StrEnum):
    INVESTIGATION = "investigation"
    SYNTHETIC_TEST = "synthetic_test"
    CLI_COMMAND = "cli_command"
    CODE_AGENT = "code_agent"


@dataclass
class TaskRecord:
    """One shell task (investigation pipeline run or subprocess-backed suite)."""

    task_id: str
    kind: TaskKind
    status: TaskStatus = TaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    result: str | None = None
    error: str | None = None
    pid: int | None = None
    command: str | None = None
    _cancel_requested: threading.Event = field(
        default_factory=threading.Event, repr=False, init=False
    )
    _process: Popen[Any] | None = field(default=None, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)
    _on_change: Callable[[], None] | None = field(default=None, repr=False, init=False)
    _rehydrated: bool = field(default=False, repr=False, init=False)

    def _notify_changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    @property
    def cancel_requested(self) -> threading.Event:
        """Set by :meth:`request_cancel`; polled by cooperative cancellation paths."""
        return self._cancel_requested

    def attach_process(self, proc: Popen[Any]) -> None:
        """Bind a child process so :meth:`request_cancel` can terminate it."""
        with self._lock:
            self._process = proc
            pid = getattr(proc, "pid", None)
            self.pid = pid if isinstance(pid, int) else None
        self._notify_changed()

    def attach_pid(self, pid: int | None) -> None:
        """Bind a previously-started process id without a live ``Popen`` object."""
        with self._lock:
            self.pid = pid
        self._notify_changed()

    def mark_running(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.RUNNING
        self._notify_changed()

    def mark_completed(self, *, result: str | None = None) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.COMPLETED
            self.result = result
            self.ended_at = time.time()
        self._notify_changed()

    def mark_cancelled(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.CANCELLED
            self.ended_at = time.time()
        self._notify_changed()

    def mark_failed(self, message: str) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.FAILED
            self.error = message
            self.ended_at = time.time()
        self._notify_changed()

    def request_cancel(self) -> bool:
        """Signal cancellation and kill a bound subprocess. Returns True if task was running."""
        mark_cancelled_without_watcher = False
        with self._lock:
            was_active = self.status == TaskStatus.RUNNING
            self._cancel_requested.set()
            proc = self._process
            pid = self.pid
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        elif was_active and pid is not None:
            mark_cancelled_without_watcher = True
        if mark_cancelled_without_watcher:
            self.mark_cancelled()
        else:
            self._notify_changed()
        return was_active

    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def refresh_rehydrated_status(self) -> None:
        """Mark persisted running tasks as finished once their PID disappears."""
        with self._lock:
            if (
                not self._rehydrated
                or self.status != TaskStatus.RUNNING
                or self._process is not None
            ):
                return
            if self.pid is not None and _process_alive(self.pid):
                return
            self.status = TaskStatus.COMPLETED
            self.result = self.result or "process exited while shell was closed"
            self.ended_at = time.time()
        self._notify_changed()

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "result": self.result,
            "error": self.error,
            "pid": self.pid,
            "command": self.command,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TaskRecord | None:
        try:
            task_id = str(data["task_id"])
            kind = TaskKind(str(data["kind"]))
            status = TaskStatus(str(data["status"]))
            started_at_value = data["started_at"]
            if not isinstance(started_at_value, int | float | str):
                return None
            started_at = float(started_at_value)
        except (KeyError, TypeError, ValueError):
            return None

        ended_at_value = data.get("ended_at")
        pid_value = data.get("pid")
        record = cls(
            task_id=task_id,
            kind=kind,
            status=status,
            started_at=started_at,
            ended_at=float(ended_at_value) if isinstance(ended_at_value, int | float) else None,
            result=str(data["result"]) if data.get("result") is not None else None,
            error=str(data["error"]) if data.get("error") is not None else None,
            pid=int(pid_value) if isinstance(pid_value, int) else None,
            command=str(data["command"]) if data.get("command") is not None else None,
        )
        record._rehydrated = True
        return record


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _tasks_store_path() -> Path:
    return const_module.OPENSRE_HOME_DIR / _TASKS_STORE_FILENAME


class TaskRegistry:
    """Recent tasks for /tasks and /cancel, optionally persisted across REPL sessions."""

    def __init__(
        self,
        *,
        max_tasks: int = _MAX_REGISTRY,
        persist_path: Path | None = None,
        load: bool = False,
    ) -> None:
        self._tasks: deque[TaskRecord] = deque(maxlen=max_tasks)
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._persist_path = persist_path
        self._max_tasks = max_tasks
        if load:
            self._load_persisted()

    @classmethod
    def persistent(cls, *, max_tasks: int = _MAX_REGISTRY) -> TaskRegistry:
        return cls(max_tasks=max_tasks, persist_path=_tasks_store_path(), load=True)

    def _attach(self, record: TaskRecord) -> TaskRecord:
        record._on_change = self._persist
        return record

    def _load_persisted(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, list):
            return
        records = [
            self._attach(record)
            for item in payload
            if isinstance(item, dict)
            if (record := TaskRecord.from_dict(item)) is not None
        ]
        for record in records[-self._max_tasks :]:
            record.refresh_rehydrated_status()
            self._tasks.append(record)
        self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        with self._persist_lock:
            with self._lock:
                payload = [task.to_dict() for task in self._tasks]
            tmp_path: Path | None = None
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp_path = self._persist_path.with_name(
                    f"{self._persist_path.name}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
                )
                tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                tmp_path.replace(self._persist_path)
            except OSError:
                if tmp_path is not None:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink()
                return

    def _refresh_rehydrated(self) -> None:
        with self._lock:
            items = list(self._tasks)
        for task in items:
            task.refresh_rehydrated_status()

    def create(self, kind: TaskKind, *, command: str | None = None) -> TaskRecord:
        task_id = secrets.token_hex(_TASK_ID_BYTES)
        record = self._attach(TaskRecord(task_id=task_id, kind=kind, command=command))
        with self._lock:
            self._tasks.append(record)
        self._persist()
        return record

    def candidates(self, task_id_prefix: str) -> list[TaskRecord]:
        self._refresh_rehydrated()
        needle = task_id_prefix.strip().lower()
        if not needle:
            return []
        with self._lock:
            items = list(self._tasks)
        return [t for t in items if t.task_id.lower().startswith(needle)]

    def get(self, task_id_prefix: str) -> TaskRecord | None:
        matches = self.candidates(task_id_prefix)
        if len(matches) != 1:
            return None
        return matches[0]

    def list_recent(self, n: int = 20) -> list[TaskRecord]:
        """Return up to ``n`` tasks, newer tasks first (FIFO buffer end is newest)."""
        self._refresh_rehydrated()
        with self._lock:
            items = list(self._tasks)
        return list(reversed(items[-n:]))

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
        self._persist()

    def __contains__(self, task_id: str) -> bool:
        return self.get(task_id) is not None


__all__ = [
    "TaskKind",
    "TaskRecord",
    "TaskRegistry",
    "TaskStatus",
]
