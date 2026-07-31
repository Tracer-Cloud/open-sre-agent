"""Persist per-attempt scoring detail from synthetic runs.

A synthetic failure is a scored verdict, not a stack trace: the suite knows the
expected category, what the agent actually answered, which required keywords
were missing, which gates failed, and how the trajectory diverged. pytest output
keeps only the first line of that, so the reason a scenario failed has to be
re-derived by hand from a truncated assertion message.

Writing one JSONL record per scored attempt keeps the whole verdict. Downstream
(``action-trace-map/scripts/synthetic_analyze.py``) turns the file into a
per-scenario table of why each run failed.

Off unless ``OPENSRE_SYNTHETIC_ARTIFACT_DIR`` is set, so normal and CI runs pay
nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

ARTIFACT_DIR_ENV = "OPENSRE_SYNTHETIC_ARTIFACT_DIR"
ARTIFACT_FILENAME = "scenario-scores.jsonl"

#: Trimmed so a record stays readable while keeping enough of the answer to see
#: what the agent actually concluded.
_ROOT_CAUSE_CHARS = 2000


def artifact_path() -> Path | None:
    """Destination file, or ``None`` when artifact capture is off."""
    raw = os.environ.get(ARTIFACT_DIR_ENV, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser() / ARTIFACT_FILENAME


def _plain(value: Any) -> Any:
    """Convert scoring dataclasses to JSON-safe structures."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _plain(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def record_scenario_score(
    *,
    suite: str,
    scenario_id: str,
    attempt: int,
    passed: bool,
    score: Any,
    final_state: dict[str, Any] | None = None,
    difficulty: int | None = None,
    failure_mode: str = "",
) -> None:
    """Append one scored attempt. No-op unless artifact capture is enabled."""
    path = artifact_path()
    if path is None:
        return
    root_cause = ""
    if final_state:
        root_cause = str(final_state.get("root_cause") or "")[:_ROOT_CAUSE_CHARS]
    record = {
        "suite": suite,
        "scenario_id": scenario_id,
        "attempt": attempt,
        "passed": bool(passed),
        "difficulty": difficulty,
        "failure_mode": failure_mode,
        "root_cause": root_cause,
        "score": _plain(score),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


__all__ = [
    "ARTIFACT_DIR_ENV",
    "ARTIFACT_FILENAME",
    "artifact_path",
    "record_scenario_score",
]
