"""Durable, redacted artifact writes for one ORCA trial."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tests.benchmarks.orcabench.artifacts.redaction import Redactor


def sha256_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


class ArtifactWriter:
    """Write structured artifacts atomically under one owned directory."""

    def __init__(self, root: Path, redactor: Redactor) -> None:
        self.root = root
        self._redactor = redactor
        self.root.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, value: Any) -> Path:
        """Redact and atomically write formatted JSON."""
        self._validate_name(name)
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python")
        safe = self._redactor.value(value)
        data = (json.dumps(safe, indent=2, sort_keys=True) + "\n").encode("utf-8")
        return self.write_bytes(name, data)

    def write_jsonl(self, name: str, values: list[Any]) -> Path:
        """Redact and atomically write one compact JSON value per line."""
        self._validate_name(name)
        lines = [json.dumps(self._redactor.value(value), sort_keys=True) for value in values]
        data = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        return self.write_bytes(name, data)

    def write_text(self, name: str, value: str, *, redact: bool = True) -> Path:
        """Write UTF-8 text, redacting known secret values by default."""
        text = self._redactor.text(value) if redact else value
        return self.write_bytes(name, text.encode("utf-8"))

    def write_bytes(self, name: str, data: bytes) -> Path:
        """Atomically replace one artifact file and fsync it."""
        self._validate_name(name)
        destination = self.root / name
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=self.root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    @staticmethod
    def _validate_name(name: str) -> None:
        path = Path(name)
        if path.is_absolute() or len(path.parts) != 1 or name in {"", ".", ".."}:
            raise ValueError(f"artifact name must be a single filename: {name!r}")
