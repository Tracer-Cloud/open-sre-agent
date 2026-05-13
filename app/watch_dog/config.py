"""Configuration helpers for the process watchdog."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.strict_config import StrictConfigModel

ThresholdName = Literal["max_cpu", "max_runtime", "max_rss"]

_DURATION_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[smh]?)$")
_BYTE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[kmgt]?b?)$", re.IGNORECASE)


@dataclass(frozen=True)
class Threshold:
    """A configured watchdog threshold."""

    name: ThresholdName
    limit: float


def parse_duration_seconds(value: float | int | str) -> float:
    """Parse a duration in seconds, or with s/m/h suffix, into seconds."""
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        text = value.strip().lower()
        match = _DURATION_RE.fullmatch(text)
        if match is None:
            raise ValueError("Expected a duration like 30, 30s, 5m, or 2h.")
        amount = float(match.group("value"))
        unit = match.group("unit") or "s"
        multiplier = {"s": 1.0, "m": 60.0, "h": 3600.0}[unit]
        seconds = amount * multiplier

    if seconds <= 0:
        raise ValueError("Duration must be positive.")
    return seconds


def parse_byte_size(value: int | float | str) -> int:
    """Parse a byte-size value with optional K/M/G/T suffix into bytes."""
    if isinstance(value, int):
        size = value
    elif isinstance(value, float):
        size = int(value)
    else:
        text = value.strip().lower()
        match = _BYTE_RE.fullmatch(text)
        if match is None:
            raise ValueError("Expected a byte size like 4096, 512M, or 4G.")
        amount = float(match.group("value"))
        unit = match.group("unit").removesuffix("b")
        multiplier = {
            "": 1,
            "k": 1024,
            "m": 1024**2,
            "g": 1024**3,
            "t": 1024**4,
        }[unit]
        size = int(amount * multiplier)

    if size <= 0:
        raise ValueError("Byte size must be positive.")
    return size


class WatchdogConfig(StrictConfigModel):
    """Validated configuration for ``opensre watchdog``."""

    pid: int | None = Field(default=None, ge=1)
    name: str | None = None
    pick_first: bool = False
    max_cpu: float | None = Field(default=None, gt=0)
    cpu_window: float = Field(default=30.0, gt=0)
    max_runtime: float | None = Field(default=None, gt=0)
    max_rss: int | None = Field(default=None, gt=0)
    interval: float = Field(default=5.0, gt=0)
    cooldown: float = Field(default=300.0, gt=0)
    once: bool = False
    chat_id: str | None = None
    verbose: bool = False

    @field_validator("cpu_window", "max_runtime", "cooldown", mode="before")
    @classmethod
    def _parse_duration_fields(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str | int | float):
            return parse_duration_seconds(value)
        return value

    @field_validator("max_rss", mode="before")
    @classmethod
    def _parse_max_rss(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str | int | float):
            return parse_byte_size(value)
        return value

    @model_validator(mode="after")
    def _validate_process_selector_and_thresholds(self) -> WatchdogConfig:
        has_pid = self.pid is not None
        has_name = bool(self.name)
        if has_pid == has_name:
            raise ValueError("Provide exactly one of pid or name.")

        if self.max_cpu is None and self.max_runtime is None and self.max_rss is None:
            raise ValueError("Configure at least one threshold.")

        return self

    def thresholds(self) -> tuple[Threshold, ...]:
        """Return thresholds in stable evaluation order."""
        thresholds: list[Threshold] = []
        if self.max_cpu is not None:
            thresholds.append(Threshold("max_cpu", self.max_cpu))
        if self.max_runtime is not None:
            thresholds.append(Threshold("max_runtime", self.max_runtime))
        if self.max_rss is not None:
            thresholds.append(Threshold("max_rss", float(self.max_rss)))
        return tuple(thresholds)
