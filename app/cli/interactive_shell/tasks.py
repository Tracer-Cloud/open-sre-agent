"""In-flight task bookkeeping for the interactive shell (REPL tasks + cancellation)."""

from __future__ import annotations

import contextlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from subprocess import Popen
from typing import Any

_TASK_ID_BYTES = 4
_MAX_REGISTRY = 100


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskKind(StrEnum):
    INVESTIGATION = "investigation"
    SYNTHETIC_TEST = "synthetic_test"


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
    _cancel_requested: threading.Event = field(
        default_factory=threading.Event, repr=False, init=False
    )
    _process: Popen[Any] | None = field(default=None, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)

    @property
    def cancel_requested(self) -> threading.Event:
        """Set by :meth:`request_cancel`; polled by cooperative cancellation paths."""
        return self._cancel_requested

    def attach_process(self, proc: Popen[Any]) -> None:
        """Bind a child process so :meth:`request_cancel` can terminate it."""
        with self._lock:
            self._process = proc

    def mark_running(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.RUNNING

    def mark_completed(self, *, result: str | None = None) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.COMPLETED
            self.result = result
            self.ended_at = time.time()

    def mark_cancelled(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.CANCELLED
            self.ended_at = time.time()

    def mark_failed(self, message: str) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.FAILED
            self.error = message
            self.ended_at = time.time()

    def request_cancel(self) -> bool:
        """Signal cancellation and kill a bound subprocess. Returns True if task was running."""
        with self._lock:
            was_active = self.status == TaskStatus.RUNNING
            self._cancel_requested.set()
            proc = self._process
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        return was_active

    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


class TaskRegistry:
    """Recent tasks for /tasks and /cancel (ring buffer, in-process only)."""

    def __init__(self, *, max_tasks: int = _MAX_REGISTRY) -> None:
        self._tasks: deque[TaskRecord] = deque(maxlen=max_tasks)
        self._lock = threading.Lock()

    def create(self, kind: TaskKind) -> TaskRecord:
        task_id = secrets.token_hex(_TASK_ID_BYTES)
        record = TaskRecord(task_id=task_id, kind=kind)
        with self._lock:
            self._tasks.append(record)
        return record

    def candidates(self, task_id_prefix: str) -> list[TaskRecord]:
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
        with self._lock:
            items = list(self._tasks)
        return list(reversed(items[-n:]))

    def __contains__(self, task_id: str) -> bool:
        return self.get(task_id) is not None


__all__ = [
    "TaskKind",
    "TaskRecord",
    "TaskRegistry",
    "TaskStatus",
]
