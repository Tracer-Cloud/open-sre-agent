from __future__ import annotations

import re
from pathlib import Path

from tests.benchmarks.realrca_graph.io import rows_by_case
from tests.benchmarks.realrca_graph.models import CandidateAnswer

TRACE_ID_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)


def trace_ids_from_answer(answer: CandidateAnswer, *, limit: int = 8) -> list[str]:
    """Return ordered trace ids explicitly present in a visible answer row."""
    values = [answer.trace_id, *TRACE_ID_RE.findall(answer.diagnosis_output)]
    return _unique_trace_ids(values, limit=limit)


def load_answer_trace_seed_map(
    paths: list[Path],
    *,
    source: str = "answer_seed",
    limit_per_case: int = 8,
) -> dict[str, list[str]]:
    """Load per-case trace seeds from visible RealRCA result files."""
    seeds: dict[str, list[str]] = {}
    for path in paths:
        for case_id, answer in rows_by_case(path, source=source).items():
            current = seeds.setdefault(case_id, [])
            current.extend(trace_ids_from_answer(answer, limit=limit_per_case))
            seeds[case_id] = _unique_trace_ids(current, limit=limit_per_case)
    return seeds


def _unique_trace_ids(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        trace_id = str(value or "").strip()
        if not TRACE_ID_RE.fullmatch(trace_id):
            continue
        normalized = trace_id.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(trace_id)
        if len(output) >= limit:
            break
    return output
