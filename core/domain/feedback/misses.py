"""Miss triage taxonomy, persistence, and conversion to benchmark scenarios.

A *miss* is an investigation whose user-facing rating was ``partial`` or
``inaccurate``. Each miss is classified into one of four root-cause buckets so
that recurring failure modes can be tracked over time and the worst offenders
can be replayed as regression scenarios in the benchmark suite.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TypedDict


class MissTaxonomy(StrEnum):
    """Top-level failure modes for an inaccurate investigation outcome.

    The four buckets map to the four levers we have to improve accuracy:
    data we fetch, how we reason over it, the tools that fetch it, and how the
    router/prompt frames the problem.
    """

    RETRIEVAL_GAP = "retrieval_gap"
    REASONING_GAP = "reasoning_gap"
    TOOL_FAILURE = "tool_failure"
    ROUTING_FAILURE = "routing_failure"
    UNKNOWN = "unknown"


# (key, human label) — used by the CLI picker and the docs alike.
_TAXONOMY_LABELS: list[tuple[MissTaxonomy, str]] = [
    (MissTaxonomy.RETRIEVAL_GAP, "Retrieval gap — missing/insufficient evidence"),
    (MissTaxonomy.REASONING_GAP, "Reasoning gap — had the evidence, wrong conclusion"),
    (MissTaxonomy.TOOL_FAILURE, "Tool failure — a tool errored or returned bad data"),
    (MissTaxonomy.ROUTING_FAILURE, "Routing/prompt failure — wrong tools/plan/prompt"),
    (MissTaxonomy.UNKNOWN, "Unknown / not sure"),
]


def taxonomy_choices() -> list[tuple[str, str]]:
    """Return ``(key, label)`` pairs in the order the picker should show them."""
    return [(t.value, label) for t, label in _TAXONOMY_LABELS]


class MissRecord(TypedDict, total=False):
    """One row in ``misses.jsonl``.

    All fields are JSON-serialisable. Optional fields may be absent on
    older records and consumers must treat the schema as additive only.
    """

    miss_id: str
    feedback_id: str
    timestamp: str
    run_id: str
    alert_name: str
    pipeline_name: str
    severity: str
    rating: str
    taxonomy: str
    taxonomy_detail: str
    root_cause: str
    root_cause_category: str
    validity_score: float | None
    investigation_loop_count: int | None
    user_id: str
    org_id: str


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
        rows = [r for r in rows if _parse_ts(r.get("timestamp")) >= cutoff]

    if taxonomy is not None:
        tax_value = taxonomy.value if isinstance(taxonomy, MissTaxonomy) else taxonomy
        rows = [r for r in rows if r.get("taxonomy") == tax_value]

    return rows


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO 8601 timestamp; unparseable values sort as the epoch."""
    if not isinstance(value, str):
        return datetime.fromtimestamp(0, tz=UTC)
    with contextlib.suppress(ValueError):
        ts = datetime.fromisoformat(value)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return datetime.fromtimestamp(0, tz=UTC)


def _grouping_key(row: MissRecord) -> tuple[str, str]:
    """Canonical ``(alert_name, taxonomy)`` key used to group misses.

    Both ``compute_recurrence`` and ``filter_top_misses`` go through this so
    the ``opensre misses stats`` recurring-pair view and the directory layout
    written by ``opensre misses export`` always agree on what counts as the
    same miss.
    """
    return (
        row.get("alert_name", "") or "<unknown>",
        row.get("taxonomy", "") or MissTaxonomy.UNKNOWN.value,
    )


def compute_recurrence(misses: list[MissRecord]) -> dict[tuple[str, str], int]:
    """Count misses grouped by ``(alert_name, taxonomy)``.

    A high count means the same alert keeps failing in the same way — the
    strongest signal that a regression scenario is warranted.
    """
    counter: Counter[tuple[str, str]] = Counter()
    for row in misses:
        counter[_grouping_key(row)] += 1
    return dict(counter)


def compute_stats(misses: list[MissRecord]) -> dict[str, Any]:
    """Summary stats used by ``opensre misses stats`` and the docs reporter.

    Returns a dict with:
      - ``total``: total misses in scope
      - ``by_taxonomy``: count per taxonomy bucket
      - ``recurring``: top ``(alert_name, taxonomy)`` pairs seen more than once
      - ``unique_alerts``: distinct alert_names in scope
    """
    by_taxonomy: Counter[str] = Counter()
    by_alert: defaultdict[str, set[str]] = defaultdict(set)
    for row in misses:
        alert, taxonomy = _grouping_key(row)
        by_taxonomy[taxonomy] += 1
        by_alert[alert].add(taxonomy)

    recurrence = compute_recurrence(misses)
    recurring = sorted(
        ((alert, tax, count) for (alert, tax), count in recurrence.items() if count > 1),
        key=lambda x: x[2],
        reverse=True,
    )

    return {
        "total": len(misses),
        "by_taxonomy": dict(by_taxonomy),
        "recurring": recurring,
        "unique_alerts": len(by_alert),
    }


def filter_top_misses(misses: list[MissRecord], top: int) -> list[MissRecord]:
    """Pick the ``top`` highest-priority misses for eval conversion.

    Priority order: most recurrent ``(alert_name, taxonomy)`` first; ties broken
    by recency. Returns one record per pair so the resulting eval set stays
    deduped — turning the *same* miss into five identical scenarios adds no
    coverage.
    """
    if top <= 0 or not misses:
        return []

    grouped: defaultdict[tuple[str, str], list[MissRecord]] = defaultdict(list)
    for row in misses:
        grouped[_grouping_key(row)].append(row)

    representative: list[tuple[int, datetime, MissRecord]] = []
    for rows in grouped.values():
        rows.sort(key=lambda r: _parse_ts(r.get("timestamp")), reverse=True)
        representative.append((len(rows), _parse_ts(rows[0].get("timestamp")), rows[0]))

    representative.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [row for _, _, row in representative[:top]]


_SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_.-]+")


def _slugify(value: str, *, fallback: str = "miss") -> str:
    cleaned = _SAFE_SLUG.sub("-", value).strip("-").lower()
    return cleaned or fallback


def to_benchmark_scenario(miss: MissRecord) -> dict[str, Any]:
    """Convert a miss into a benchmark scenario ``alert.json`` payload.

    The shape matches benchmark scenario ``alert.json`` payloads so the
    benchmark runner can consume the exported scenarios with no adapter changes.

    The grading rubric lives at ``commonAnnotations.scoring_points`` — that is
    where :func:`integrations.opensre.extract_scoring_points` looks for
    it (``opensre investigate --evaluate``), and where
    :func:`integrations.opensre.strip_scoring_points_from_alert` strips it before
    handing the alert to the agent. Putting it under ``_meta`` would both be
    invisible to the judge *and* leak the answer to the agent.
    """
    miss_id = miss.get("miss_id", str(uuid.uuid4()))
    alert_name = miss.get("alert_name") or "production miss"
    root_cause = miss.get("root_cause") or ""
    detail = miss.get("taxonomy_detail") or ""
    taxonomy = miss.get("taxonomy") or MissTaxonomy.UNKNOWN.value

    return {
        "_meta": {
            "purpose": "Regression scenario derived from a production miss",
            "source": "opensre misses export",
            "miss_id": miss_id,
            "original_run_id": miss.get("run_id", ""),
            "captured_at": miss.get("timestamp", ""),
            "taxonomy": taxonomy,
        },
        "title": f"[Regression] {alert_name}",
        "alert_name": alert_name,
        "pipeline_name": miss.get("pipeline_name", ""),
        "severity": miss.get("severity") or "warning",
        "alert_source": "closed_loop_learning",
        "message": detail or alert_name,
        "text": detail or alert_name,
        "commonLabels": {
            "pipeline_name": miss.get("pipeline_name", ""),
            "severity": miss.get("severity") or "warning",
            "taxonomy": taxonomy,
        },
        "commonAnnotations": {
            "summary": detail or alert_name,
            "miss_id": miss_id,
            "taxonomy": taxonomy,
            "scoring_points": {
                "expected_root_cause": root_cause,
                "expected_category": miss.get("root_cause_category", ""),
                "miss_notes": detail,
            },
        },
    }


def export_scenarios(
    misses: list[MissRecord],
    out_dir: Path,
) -> list[Path]:
    """Write one ``alert.json`` per miss under ``out_dir/<slug>/``.

    Returns the paths written. The caller is responsible for creating any
    enclosing benchmark config — this function only produces the per-case
    alert payloads that the existing runner already understands.
    """
    written: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for index, miss in enumerate(misses, start=1):
        # ``or`` rather than dict.get default: a JSON null stored on disk
        # returns Python None, which would crash _slugify's re.sub.
        slug = _slugify(miss.get("alert_name") or "", fallback=f"miss-{index:04d}")
        taxonomy_slug = _slugify(miss.get("taxonomy") or "unknown", fallback="unknown")
        case_dir = out_dir / f"{index:04d}_{slug}_{taxonomy_slug}"
        case_dir.mkdir(parents=True, exist_ok=True)

        scenario = to_benchmark_scenario(miss)
        target = case_dir / "alert.json"
        target.write_text(
            json.dumps(scenario, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written.append(target)

    return written


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
