"""Privacy controls for the interactive-shell command history.

Built-in redaction patterns + a ``RedactingFileHistory`` that subclasses
``prompt_toolkit.FileHistory`` to apply redaction before each entry is
persisted. Settings resolve from env vars and the ``interactive.history``
section of ``~/.config/opensre/config.yml``; built-in defaults keep
redaction on, persistence on, and entries capped at 5000.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from prompt_toolkit.history import FileHistory

DEFAULT_MAX_ENTRIES = 5000


@dataclass(frozen=True)
class RedactionRule:
    """One named regex with its replacement."""

    name: str
    pattern: re.Pattern[str]
    replacement: str


def _build_default_rules() -> tuple[RedactionRule, ...]:
    raw: list[tuple[str, str, str]] = [
        ("aws_key", r"(?:AKIA|ASIA)[A-Z0-9]{16}", "[REDACTED:aws_key]"),
        (
            "aws_secret",
            r"(?i)aws_secret_access_key[\s=:]+[A-Za-z0-9/+=]{40}",
            "aws_secret_access_key=[REDACTED:aws_secret]",
        ),
        ("github_pat_classic", r"ghp_[A-Za-z0-9]{36}", "[REDACTED:github_pat]"),
        ("github_pat_fine", r"github_pat_[A-Za-z0-9_]{82}", "[REDACTED:github_pat]"),
        ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{40,}", "[REDACTED:anthropic_key]"),
        ("openai_key", r"sk-(?!ant-)[A-Za-z0-9_\-]{20,}", "[REDACTED:openai_key]"),
        ("slack_token", r"xox[bopas]-[A-Za-z0-9-]{10,}", "[REDACTED:slack_token]"),
        ("stripe_key", r"sk_(?:live|test)_[A-Za-z0-9]{24,}", "[REDACTED:stripe_key]"),
        ("bearer", r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}", "Bearer [REDACTED]"),
        (
            "jwt",
            r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
            "[REDACTED:jwt]",
        ),
        ("password_arg", r"(?i)(--password=|password=)\S+", "[REDACTED:password]"),
        (
            "private_key",
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            "[REDACTED:private_key]",
        ),
    ]
    return tuple(RedactionRule(name, re.compile(p), repl) for (name, p, repl) in raw)


DEFAULT_REDACTION_RULES: tuple[RedactionRule, ...] = _build_default_rules()


def redact_text(text: str, rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES) -> str:
    """Apply each rule's pattern in declared order, replacing every match."""
    for rule in rules:
        text = rule.pattern.sub(rule.replacement, text)
    return text


@dataclass(frozen=True)
class HistoryPolicy:
    """Resolved privacy policy for the prompt-history persistence layer."""

    enabled: bool = True
    redact: bool = True
    max_entries: int = DEFAULT_MAX_ENTRIES

    _ENV_ENABLED: ClassVar[str] = "OPENSRE_HISTORY_ENABLED"
    _ENV_REDACT: ClassVar[str] = "OPENSRE_HISTORY_REDACT"
    _ENV_MAX_ENTRIES: ClassVar[str] = "OPENSRE_HISTORY_MAX_ENTRIES"

    @classmethod
    def load(cls, file_settings: dict[str, Any] | None = None) -> HistoryPolicy:
        """Resolve from env -> file -> defaults (env wins)."""
        file_settings = file_settings or {}
        return cls(
            enabled=_parse_bool(os.getenv(cls._ENV_ENABLED), file_settings.get("enabled"), True),
            redact=_parse_bool(os.getenv(cls._ENV_REDACT), file_settings.get("redact"), True),
            max_entries=_parse_int(
                os.getenv(cls._ENV_MAX_ENTRIES),
                file_settings.get("max_entries"),
                DEFAULT_MAX_ENTRIES,
            ),
        )


def _parse_bool(env_val: str | None, file_val: Any, default: bool) -> bool:
    if env_val is not None and env_val != "":
        return env_val.strip().lower() not in {"0", "false", "off", "no"}
    if isinstance(file_val, bool):
        return file_val
    if isinstance(file_val, str) and file_val.strip() != "":
        return file_val.strip().lower() not in {"0", "false", "off", "no"}
    if file_val is not None:
        return bool(file_val)
    return default


def _parse_int(env_val: str | None, file_val: Any, default: int) -> int:
    if env_val is not None and env_val.strip():
        try:
            return max(0, int(env_val.strip()))
        except ValueError:
            return default
    if isinstance(file_val, int) and file_val >= 0:
        return file_val
    return default


class RedactingFileHistory(FileHistory):
    """``FileHistory`` that redacts known token shapes before persisting.

    Also enforces a max-entry retention cap by rewriting the file when
    the new total exceeds the cap. ``paused`` lets ``/history off`` stop
    persistence at runtime without swapping the History instance.
    """

    def __init__(
        self,
        filename: str,
        *,
        rules: tuple[RedactionRule, ...] = DEFAULT_REDACTION_RULES,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        super().__init__(filename)
        self.paused: bool = False
        self._rules = rules
        self._max_entries = max_entries
        self._entry_count: int | None = None

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def set_max_entries(self, max_entries: int, *, prune: bool = True) -> None:
        self._max_entries = max(0, max_entries)
        if prune:
            self._prune_to_cap()

    def store_string(self, string: str) -> None:
        if self.paused:
            return
        cleaned = redact_text(string, self._rules) if self._rules else string
        super().store_string(cleaned)
        if self._max_entries <= 0:
            return
        if self._entry_count is None:
            self._entry_count = self._count_entries()
        else:
            self._entry_count += 1
        if self._entry_count > self._max_entries:
            self._prune_to_cap()

    def _prune_to_cap(self) -> None:
        if self._max_entries <= 0:
            return
        path = Path(os.fsdecode(self.filename))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        lines = text.splitlines(keepends=True)
        # Each persisted entry begins with a "# <timestamp>" comment line.
        boundaries = [i for i, ln in enumerate(lines) if ln.startswith("# ")]
        if len(boundaries) <= self._max_entries:
            self._entry_count = len(boundaries)
            return

        keep_from = boundaries[len(boundaries) - self._max_entries]
        if keep_from > 0 and lines[keep_from - 1].strip() == "":
            keep_from -= 1
        try:
            path.write_text("".join(lines[keep_from:]), encoding="utf-8")
            self._entry_count = self._max_entries
        except OSError:
            return

    def _count_entries(self) -> int:
        path = Path(os.fsdecode(self.filename))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        return sum(1 for line in text.splitlines() if line.startswith("# "))


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_REDACTION_RULES",
    "HistoryPolicy",
    "RedactingFileHistory",
    "RedactionRule",
    "redact_text",
]
