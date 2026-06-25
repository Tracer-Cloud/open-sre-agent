"""Agentic AI for Data Pipeline Incident Resolution Demo."""

from __future__ import annotations

from typing import Any

__all__ = ["run_investigation"]


def __getattr__(name: str) -> Any:
    if name == "run_investigation":
        from app.utils.sdk import run_investigation

        return run_investigation
    raise AttributeError(name)
