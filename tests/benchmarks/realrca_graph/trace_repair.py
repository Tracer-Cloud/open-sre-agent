from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from tests.benchmarks.realrca_graph.features import (
    flatten_strings,
    text_for_features,
    token_features,
)
from tests.benchmarks.realrca_graph.models import CandidateAnswer

REAL_TRACE_ID_RE = re.compile(r"^[0-9a-f]{24,40}$", re.IGNORECASE)


def is_real_trace_id(value: str) -> bool:
    """Return whether ``value`` has the shape of a Sunfire trace id."""

    return bool(REAL_TRACE_ID_RE.fullmatch(value.strip()))


def _trace_candidates_from_graph(graph_context: dict[str, Any]) -> list[tuple[float, str, Any]]:
    candidates: list[tuple[float, str, Any]] = []
    for index, raw in enumerate(graph_context.get("root_candidates") or [], start=1):
        if not isinstance(raw, dict):
            continue
        props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
        trace_id = str(props.get("trace_id") or "").strip()
        if not is_real_trace_id(trace_id):
            continue
        base_score = 10.0 - min(index, 20) * 0.1
        with suppress(TypeError, ValueError):
            base_score += float(raw.get("score") or 0.0)
        if str(raw.get("kind") or "") == "trace_span":
            base_score += 1.0
        candidates.append((base_score, trace_id.lower(), raw))

    for index, raw in enumerate(graph_context.get("evidence") or [], start=1):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        command = str(raw.get("command") or "")
        if "trace" not in name.lower() and "trace get" not in command.lower():
            continue
        for text in flatten_strings(
            {"name": name, "command": command, "summary": raw.get("summary")}
        ):
            for trace_id in re.findall(r"\b[0-9a-f]{24,40}\b", text, re.IGNORECASE):
                candidates.append((4.0 - min(index, 20) * 0.05, trace_id.lower(), raw))
    return candidates


def _values(tokens: set[str], prefix: str) -> set[str]:
    return {token.removeprefix(prefix) for token in tokens if token.startswith(prefix)}


def _has_prefixed(tokens: set[str], prefixes: tuple[str, ...]) -> bool:
    return any(token.startswith(prefix) for token in tokens for prefix in prefixes)


def _matches_domain(answer: CandidateAnswer, answer_tokens: set[str], raw: Any) -> bool:
    candidate_tokens = token_features(raw)
    candidate_text = text_for_features(raw).lower()
    answer_text = answer.diagnosis_output.lower()

    answer_services = _values(answer_tokens, "service:")
    answer_methods = _values(answer_tokens, "method:")
    candidate_services = _values(candidate_tokens, "service:")
    candidate_methods = _values(candidate_tokens, "method:")
    if (answer_services or answer_methods) and not (
        answer_services & candidate_services or answer_methods & candidate_methods
    ):
        return False

    if "keyword:sql" in answer_tokens:
        return (
            _has_prefixed(
                candidate_tokens,
                ("sql_op:", "sql_db:", "sql_table:", "sql_id:", "rds:"),
            )
            or "tddl" in candidate_text
            or "db@" in candidate_text
        )

    if "keyword:mq" in answer_tokens:
        return any(
            marker in candidate_text
            for marker in ("metaq", "rocketmq", "mqrecv", "topic", "broker")
        )

    if "keyword:cache" in answer_tokens:
        if not any(marker in candidate_text for marker in ("tair", "redis")):
            return False
        is_write_answer = "write" in answer_text or "写" in answer_text
        is_read_answer = "read" in answer_text or "读" in answer_text
        if is_write_answer and re.search(r"\bget:", candidate_text):
            return False
        return not (is_read_answer and re.search(r"\b(?:set|put|incr|delete):", candidate_text))

    if "keyword:thread_pool" in answer_tokens or "gc" in answer_text or "cpu" in answer_text:
        return bool(_values(answer_tokens, "app:") & _values(candidate_tokens, "app:"))

    return True


def best_replacement_trace_id(
    answer: CandidateAnswer,
    graph_context: dict[str, Any],
    *,
    allow_inferred: bool = False,
) -> str:
    """Choose the graph-supported trace id that best matches ``answer``."""

    if is_real_trace_id(answer.trace_id):
        return answer.trace_id

    answer_tokens = token_features(answer.diagnosis_output)
    ranked: list[tuple[float, str]] = []
    seen: set[str] = set()
    for base_score, trace_id, raw in _trace_candidates_from_graph(graph_context):
        if trace_id in seen:
            continue
        seen.add(trace_id)
        if not _matches_domain(answer, answer_tokens, raw):
            continue
        if not allow_inferred and trace_id not in answer.diagnosis_output.lower():
            continue
        raw_tokens = token_features(raw)
        overlap = len(answer_tokens & raw_tokens)
        in_answer_bonus = 3.0 if trace_id in answer.diagnosis_output.lower() else 0.0
        ranked.append((base_score + overlap + in_answer_bonus, trace_id))
    if not ranked:
        return answer.trace_id
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1]


def repair_trace_id(
    answer: CandidateAnswer,
    graph_context: dict[str, Any],
    *,
    allow_inferred: bool = False,
) -> CandidateAnswer:
    """Return ``answer`` with only an invalid trace id replaced when possible."""

    trace_id = best_replacement_trace_id(answer, graph_context, allow_inferred=allow_inferred)
    if trace_id == answer.trace_id:
        return answer
    return CandidateAnswer(
        source=answer.source,
        case_id=answer.case_id,
        diagnosis_output=answer.diagnosis_output,
        trace_id=trace_id,
    )
