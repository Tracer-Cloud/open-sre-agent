"""Miss JSONL persistence and time-window parsing."""

from __future__ import annotations

import contextlib
import json
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.domain.feedback.misses.taxonomy import MissRecord, MissTaxonomy


def _config_dir() -> Path:
    from config.constants import OPENSRE_HOME_DIR

    return OPENSRE_HOME_DIR


def misses_path() -> Path:
    """Path to the on-disk JSONL store. Created lazily by :func:`record_miss`."""
    return _config_dir() / "misses.jsonl"


def record_miss(
    feedback_record: dict[str, Any],
    *,
    taxonomy: MissTaxonomy | str,
    taxonomy_detail: str = "",
    final_state: dict[str, Any] | None = None,
) -> MissRecord | None:
    """Persist a miss record derived from a feedback submission.

    ``feedback_record`` is the dict the feedback prompt already builds.
    ``final_state`` is the investigation ``AgentState`` and is used to backfill
    provenance fields that are not in the feedback dict (``pipeline_name``,
    ``severity``).

    Returns the persisted record on success, ``None`` if the JSONL append
    failed (disk full, permissions). Write errors are printed to stderr so the
    user sees them; callers must not show a "saved" confirmation or emit
    downstream analytics for a ``None`` result.
    """
    tax_value = taxonomy.value if isinstance(taxonomy, MissTaxonomy) else taxonomy
    state = final_state or {}

    record: MissRecord = {
        "miss_id": str(uuid.uuid4()),
        "feedback_id": feedback_record.get("feedback_id", ""),
        "timestamp": feedback_record.get("timestamp") or datetime.now(UTC).isoformat(),
        "run_id": feedback_record.get("run_id", ""),
        "alert_name": feedback_record.get("alert_name", ""),
        "pipeline_name": state.get("pipeline_name", ""),
        "severity": state.get("severity", ""),
        "rating": feedback_record.get("rating", ""),
        "taxonomy": tax_value,
        "taxonomy_detail": (taxonomy_detail or feedback_record.get("note") or "")[:1000],
        "root_cause": (feedback_record.get("root_cause") or "")[:500],
        "root_cause_category": feedback_record.get("root_cause_category", ""),
        "validity_score": feedback_record.get("validity_score"),
        "investigation_loop_count": feedback_record.get("investigation_loop_count"),
        "user_id": feedback_record.get("user_id", ""),
        "org_id": feedback_record.get("org_id", ""),
    }

    path = misses_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"opensre: could not record miss to {path}: {exc}", file=sys.stderr)
        return None

    return record


def load_misses(
    *,
    since: datetime | None = None,
    taxonomy: MissTaxonomy | str | None = None,
    path: Path | None = None,
) -> list[MissRecord]:
    """Read misses from disk, newest last.

    Malformed lines are skipped so a single bad record cannot poison the
    whole store. ``since`` and ``taxonomy`` are applied in-memory.
    """
    target = path or misses_path()
    if not target.exists():
        return []

    rows: list[MissRecord] = []
    with contextlib.suppress(OSError), target.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            rows.append(row)  # type: ignore[arg-type]

    if since is not None:
        cutoff = since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)
        rows = [r for r in rows if parse_timestamp(r.get("timestamp")) >= cutoff]

    if taxonomy is not None:
        tax_value = taxonomy.value if isinstance(taxonomy, MissTaxonomy) else taxonomy
        rows = [r for r in rows if r.get("taxonomy") == tax_value]

    return rows


def parse_timestamp(value: Any) -> datetime:
    """Parse an ISO 8601 timestamp; unparseable values sort as the epoch."""
    if not isinstance(value, str):
        return datetime.fromtimestamp(0, tz=UTC)
    with contextlib.suppress(ValueError):
        ts = datetime.fromisoformat(value)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return datetime.fromtimestamp(0, tz=UTC)


def parse_since(spec: str) -> datetime:
    """Parse a CLI-friendly ``--since`` token.

    Accepts a number followed by ``d`` (days), ``h`` (hours), ``w`` (weeks),
    or an ISO 8601 timestamp. Raises ``ValueError`` on unrecognised input so
    Click can surface a clean error message.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty --since value")

    match = re.fullmatch(r"(\d+)\s*([dhw])", spec.lower())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {
            "d": timedelta(days=amount),
            "h": timedelta(hours=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return datetime.now(UTC) - delta

    parsed = datetime.fromisoformat(spec)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "load_misses",
    "misses_path",
    "parse_since",
    "parse_timestamp",
    "record_miss",
]
