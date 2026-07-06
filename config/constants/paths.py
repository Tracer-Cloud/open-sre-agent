from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT

__all__ = ["PROJECT_ROOT", "REPO_ROOT"]
