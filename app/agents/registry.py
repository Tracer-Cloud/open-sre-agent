"""Agent record storage and JSONL-backed registry for tracked local processes."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from app.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = OPENSRE_HOME_DIR / "agents.jsonl"


@dataclass(frozen=True)
class AgentRecord:
    """Immutable snapshot of a registered local AI agent process."""

    name: str
    pid: int
    command: str
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: str = "registered"
    waits_on: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> AgentRecord:
        raw_pid = data["pid"]
        raw_waits = data.get("waits_on", [])
        pid = int(str(raw_pid))
        waits_on = tuple(int(str(p)) for p in raw_waits) if isinstance(raw_waits, list) else ()
        return cls(
            name=str(data["name"]),
            pid=pid,
            command=str(data["command"]),
            registered_at=str(data.get("registered_at", datetime.now(UTC).isoformat())),
            source=str(data.get("source", "registered")),
            waits_on=waits_on,
        )

    def add_waits_on(self, record: AgentRecord) -> AgentRecord:
        """Create a new record instead mutating to maintain the immutable contract."""
        if record.pid in self.waits_on:
            return self
        return replace(self, waits_on=(*self.waits_on, record.pid))


class AgentRegistry:
    """JSONL-backed registry of locally running AI agent processes.

    Persists to ``~/.config/opensre/agents.jsonl`` by default.  The file is
    append-only for ``register`` and fully rewritten on ``forget`` / ``clear``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_REGISTRY_PATH
        self._records: dict[int, AgentRecord] = {}
        self._load_from_disk()

    def register(self, record: AgentRecord) -> None:
        overwrite = record.pid in self._records
        self._records[record.pid] = record
        if overwrite:
            self._rewrite()
        else:
            self._append(record)

    def forget(self, pid: int) -> AgentRecord | None:
        removed = self._records.pop(pid, None)
        if removed is not None:
            self._scrub_waits_on({pid})
            self._rewrite()
        return removed

    def forget_many(self, pids: Iterable[int]) -> list[AgentRecord]:
        """Remove every PID in ``pids`` and rewrite the JSONL file once.

        Functionally equivalent to calling :meth:`forget` in a loop,
        but does only **one** disk rewrite at the end. Use this when
        pruning many records at once (e.g. the boot-time sweep) so a
        registry holding N dead PIDs doesn't trigger N full rewrites.
        Returns the records that were actually removed (silently
        skips PIDs not present in the registry).
        """
        removed: list[AgentRecord] = []
        for pid in pids:
            record = self._records.pop(pid, None)
            if record is not None:
                removed.append(record)
        if removed:
            self._scrub_waits_on({r.pid for r in removed})
            self._rewrite()
        return removed

    def _scrub_waits_on(self, removed_pids: set[int]) -> None:
        """Drop ``removed_pids`` from every remaining record's ``waits_on``."""
        for pid, record in self._records.items():
            if not record.waits_on:
                continue
            left = tuple(p for p in record.waits_on if p not in removed_pids)
            if len(left) != len(record.waits_on):
                self._records[pid] = replace(record, waits_on=left)

    def list(self) -> list[AgentRecord]:
        return list(self._records.values())

    def get(self, pid: int) -> AgentRecord | None:
        return self._records.get(pid)

    def clear(self) -> None:
        self._records.clear()
        self._rewrite()

    def _load_from_disk(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            logger.warning("Failed to read agent registry from %s", self._path)
            return
        for line in lines:
            try:
                data = json.loads(line)
                record = AgentRecord.from_dict(data)
                self._records[record.pid] = record
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                logger.warning("Skipping corrupt agent registry line: %s", line[:80])

    def _append(self, record: AgentRecord) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict()) + "\n")
        except OSError:
            logger.warning("Failed to append to agent registry at %s", self._path)

    def _rewrite(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for record in self._records.values():
                    fh.write(json.dumps(record.to_dict()) + "\n")
            tmp.replace(self._path)
        except OSError:
            logger.warning("Failed to rewrite agent registry at %s", self._path)
