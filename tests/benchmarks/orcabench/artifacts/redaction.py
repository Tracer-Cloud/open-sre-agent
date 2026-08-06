"""Credential redaction shared by every persisted structured artifact."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

REDACTED = "[REDACTED]"
_SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_NON_SECRET_COUNTER_KEYS = frozenset(
    {
        "cache_tokens",
        "cached_tokens",
        "input_tokens",
        "native_max_output_tokens",
        "output_tokens",
    }
)


class Redactor:
    """Convert arbitrary state to JSON-safe values while removing credentials."""

    def __init__(self, known_secrets: Sequence[str] = ()) -> None:
        self._known_secrets = tuple(
            sorted({value for value in known_secrets if value}, key=len, reverse=True)
        )

    def text(self, value: str) -> str:
        """Replace known secret values in free-form text."""
        redacted = value
        for secret in self._known_secrets:
            redacted = redacted.replace(secret, REDACTED)
        return redacted

    def value(self, value: Any, *, key: str | None = None) -> Any:
        """Recursively convert and redact a value for JSON serialization."""
        if key is not None and self._is_secret_key(key):
            return REDACTED
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, Enum):
            return self.value(value.value)
        if isinstance(value, BaseModel):
            return self.value(value.model_dump(mode="python"))
        if is_dataclass(value) and not isinstance(value, type):
            return self.value(asdict(value))
        if isinstance(value, Mapping):
            return {
                str(item_key): self.value(item, key=str(item_key))
                for item_key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return [self.value(item) for item in value]
        if isinstance(value, (bytes, bytearray)):
            return {"type": type(value).__name__, "length": len(value)}
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "representation": self.text(repr(value)),
        }

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        normalized = key.lower()
        if normalized in _NON_SECRET_COUNTER_KEYS:
            return False
        return any(part in normalized for part in _SECRET_KEY_PARTS)
