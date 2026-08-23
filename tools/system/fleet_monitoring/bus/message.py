"""Bus message dataclass and wire encoding."""

from __future__ import annotations

import json
import types
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Bus message wire-format version. Bump when ``BusMessage`` fields change shape.
BUS_SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class BusMessage:
    """A single finding published on the agent bus.

    Field shape mirrors ``AgentState.evidence`` entries so a message can be
    folded into investigation state without renaming. ``agent`` follows the
    ``"<name>:<pid>"`` convention used by ``tools.system.fleet_monitoring.conflicts.WriteEvent``.

    ``data`` is wrapped in ``types.MappingProxyType`` at construction so the
    payload is read-only post-init; mutating ``msg.data["x"] = 1`` raises
    ``TypeError``. ``__hash__`` is explicitly disabled because ``data`` is a
    mapping and would otherwise produce a misleading auto-generated hash that
    fails at call time.
    """

    agent: str
    topic: str
    summary: str
    source: str = ""
    path: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = BUS_SCHEMA_VERSION

    # Disable hashing: a BusMessage carries a mapping and is not a value-key.
    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Defensive copy + read-only view: protects against both external
        # mutation of the original dict and ``msg.data["x"] = 1`` after
        # construction. ``object.__setattr__`` bypasses the frozen check.
        object.__setattr__(self, "data", types.MappingProxyType(dict(self.data)))

    def to_jsonl(self) -> bytes:
        """Encode as a single newline-terminated JSON frame ready for the socket."""
        payload = {
            "agent": self.agent,
            "topic": self.topic,
            "summary": self.summary,
            "source": self.source,
            "path": self.path,
            "data": dict(self.data),
            "id": self.id,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def from_jsonl(cls, line: bytes | str) -> BusMessage:
        """Decode one JSONL frame into a ``BusMessage``. Raises on malformed input."""
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("bus frame must be a JSON object")
        return cls(
            agent=str(data["agent"]),
            topic=str(data["topic"]),
            summary=str(data["summary"]),
            source=str(data.get("source", "")),
            path=str(data.get("path", "")),
            data=dict(data.get("data", {})),
            id=str(data.get("id", uuid.uuid4())),
            timestamp=str(data.get("timestamp", datetime.now(UTC).isoformat())),
            schema_version=int(data.get("schema_version", BUS_SCHEMA_VERSION)),
        )
