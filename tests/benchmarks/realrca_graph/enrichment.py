from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from tests.benchmarks.realrca_graph.alignment import critical_tokens
from tests.benchmarks.realrca_graph.features import text_for_features, token_features
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore, EvidenceBundle
from tests.benchmarks.realrca_graph.trajectory_mining import answer_contains
from tests.benchmarks.realrca_graph.verifier import score_candidate

HIGH_SIGNAL_KINDS = {
    "exception",
    "hsf_code",
    "tddl_code",
    "ora_code",
    "sqlstate",
    "rds",
    "strong_phrase",
}
SQL_EXCEPTION_MARKERS = (
    "sql",
    "jdbc",
    "druid",
    "duplicate",
    "constraint",
    "communications",
    "connection",
    "tddl",
)
NOISY_TERMS = {
    "AttributeError",
    "CalledProcessError",
    "FileNotFoundError",
    "JSONDecodeError",
    "KeyError",
    "RuntimeError",
    "TypeError",
    "ValueError",
    "java.lang.RuntimeException",
    "RuntimeException",
    "com.taobao.hsf.exception.HSFException",
    "HSFException",
}
GENERIC_EXCEPTION_SIMPLE_NAMES = {
    "BizException",
    "BusinessException",
    "ClientAbortException",
    "Exception",
    "IOException",
    "NullPointerException",
    "RuntimeException",
    "ServiceException",
    "SystemException",
    "TkException",
}
WRAPPER_EXCEPTION_SUFFIXES = (
    "bizexception",
    "businessexception",
    "systemexception",
)
SQL_KINDS = {"tddl_code", "ora_code", "sqlstate", "rds"}
TOOL_RESULT_EVENT = "agent.tool_result"


@dataclass(frozen=True)
class TrajectoryTerm:
    """One high-signal term mined from visible trajectory evidence."""

    term: str
    kind: str
    score: int
    count: int = 0
    graph_supported: bool = False
    event_counts: Counter[str] = field(default_factory=Counter)
    snippets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_counts"] = dict(self.event_counts)
        return payload


@dataclass(frozen=True)
class EnrichmentDecision:
    """Auditable deterministic evidence-enrichment decision for one case."""

    case_id: str
    changed: bool
    reason: str
    selected_terms: list[TrajectoryTerm]
    rejected_terms: list[dict[str, Any]]
    candidate: CandidateAnswer
    score: CandidateScore

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "changed": self.changed,
            "reason": self.reason,
            "selected_terms": [term.to_dict() for term in self.selected_terms],
            "rejected_terms": self.rejected_terms,
            "candidate": self.candidate.to_result_row(),
            "score": self.score.to_dict(),
        }


def terms_from_audit_case(audit_case: dict[str, Any]) -> list[TrajectoryTerm]:
    """Parse a trajectory audit case into reusable term objects."""

    terms: list[TrajectoryTerm] = []
    for raw in audit_case.get("missing_terms") or []:
        if not isinstance(raw, dict):
            continue
        event_counts = Counter(
            {
                str(key): int(value)
                for key, value in (raw.get("event_counts") or {}).items()
                if isinstance(value, int | float)
            }
        )
        snippets: list[str] = []
        for occurrence in raw.get("occurrences") or []:
            if isinstance(occurrence, dict) and isinstance(occurrence.get("snippet"), str):
                snippets.append(occurrence["snippet"])
        terms.append(
            TrajectoryTerm(
                term=str(raw.get("term") or "").strip(),
                kind=str(raw.get("kind") or "").strip(),
                score=int(raw.get("score") or 0),
                count=int(raw.get("count") or 0),
                graph_supported=bool(raw.get("graph_supported")),
                event_counts=event_counts,
                snippets=snippets,
            )
        )
    return terms


def enrich_answer(
    baseline: CandidateAnswer,
    bundle: EvidenceBundle,
    terms: list[TrajectoryTerm],
    *,
    max_terms: int = 3,
    max_answer_chars: int = 1200,
    min_term_score: int = 18,
) -> EnrichmentDecision:
    """Append only root-aligned evidence terms while preserving the baseline root cause."""

    selected: list[TrajectoryTerm] = []
    rejected: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for term in sorted(terms, key=lambda item: (-item.score, item.kind, item.term)):
        accepted, reason = _term_acceptance_reason(
            baseline,
            bundle,
            term,
            min_term_score=min_term_score,
        )
        if not accepted:
            rejected.append({"term": term.to_dict(), "reason": reason})
            continue
        key = _canonical_term_key(term)
        if key in seen_terms:
            rejected.append({"term": term.to_dict(), "reason": "duplicate selected evidence term"})
            continue
        seen_terms.add(key)
        selected.append(term)
        if len(selected) >= max_terms:
            break

    if not selected:
        candidate = baseline
        score = score_candidate(candidate, baseline, bundle)
        return EnrichmentDecision(
            case_id=baseline.case_id,
            changed=False,
            reason="kept baseline: no root-aligned high-signal trajectory terms",
            selected_terms=[],
            rejected_terms=rejected[:12],
            candidate=candidate,
            score=score,
        )

    patch = _render_evidence_patch(selected)
    diagnosis = _append_patch(baseline.diagnosis_output, patch, max_chars=max_answer_chars)
    changed = diagnosis != baseline.diagnosis_output
    candidate = CandidateAnswer(
        source="trajectory-evidence-enrichment",
        case_id=baseline.case_id,
        diagnosis_output=diagnosis,
        trace_id=baseline.trace_id,
    )
    score = score_candidate(candidate, baseline, bundle)
    if not changed:
        return EnrichmentDecision(
            case_id=baseline.case_id,
            changed=False,
            reason="kept baseline: answer would exceed max length after evidence patch",
            selected_terms=[],
            rejected_terms=rejected[:12],
            candidate=baseline,
            score=score_candidate(baseline, baseline, bundle),
        )
    return EnrichmentDecision(
        case_id=baseline.case_id,
        changed=True,
        reason=f"appended {len(selected)} root-aligned high-signal evidence terms",
        selected_terms=selected,
        rejected_terms=rejected[:12],
        candidate=candidate,
        score=score,
    )


def _term_acceptance_reason(
    baseline: CandidateAnswer,
    bundle: EvidenceBundle,
    term: TrajectoryTerm,
    *,
    min_term_score: int,
) -> tuple[bool, str]:
    if not term.term:
        return False, "empty term"
    if term.kind not in HIGH_SIGNAL_KINDS:
        return False, f"unsupported term kind: {term.kind}"
    if term.term in NOISY_TERMS:
        return False, "generic process or framework exception"
    if term.kind == "exception" and not _is_discriminative_exception(term.term):
        return False, "generic wrapper exception"
    if answer_contains(baseline.diagnosis_output, term.term) or _semantically_covered(
        baseline.diagnosis_output,
        term,
    ):
        return False, "already covered by baseline answer"
    if not _mechanism_compatible(baseline.diagnosis_output, term):
        return False, "term conflicts with baseline failure mechanism"
    if term.score < min_term_score:
        return False, f"term score below threshold: {term.score}"
    if not term.graph_supported:
        return False, "term is not present in graph evidence"
    if term.event_counts.get(TOOL_RESULT_EVENT, 0) <= 0:
        return False, "term does not appear in a tool result"
    if not _domain_compatible(baseline, bundle, term):
        return False, "term kind conflicts with case/root domain"
    if not _root_aligned(baseline, bundle, term):
        return False, "term is not aligned with baseline root entities"
    return True, "accepted"


def _domain_compatible(
    baseline: CandidateAnswer, bundle: EvidenceBundle, term: TrajectoryTerm
) -> bool:
    text = " ".join(
        [
            baseline.diagnosis_output,
            bundle.case_type,
            " ".join(item.root_layer for item in bundle.hypotheses[:3]),
        ]
    ).lower()
    if term.kind == "hsf_code":
        return "hsf" in text
    if term.kind in SQL_KINDS:
        return any(marker in text for marker in ("sql", "tddl", "rds", "数据库", "慢查询", "慢sql"))
    if term.kind == "exception" and any(
        marker in text for marker in ("sql", "tddl", "rds", "数据库", "慢查询", "慢sql")
    ):
        return any(marker in term.term.lower() for marker in SQL_EXCEPTION_MARKERS)
    if term.kind == "strong_phrase":
        lower_term = term.term.lower()
        if "sentinel" in lower_term or "限流" in lower_term or "熔断" in lower_term:
            return any(marker in text for marker in ("sentinel", "限流", "熔断", "block"))
        if "timeout" in lower_term or "超时" in lower_term:
            return "timeout" in text or "超时" in text or "hsf" in text
    return True


def _mechanism_compatible(answer: str, term: TrajectoryTerm) -> bool:
    answer_mechanisms = _baseline_mechanisms(answer)
    if not answer_mechanisms:
        return True
    term_mechanisms = _term_mechanisms(term)
    if term_mechanisms:
        return bool(answer_mechanisms & term_mechanisms)
    strict_mechanisms = {
        "address",
        "cache",
        "cpu",
        "database",
        "idempotency",
        "jvm",
        "limit",
        "message_queue",
        "thread_pool",
    }
    return not bool(answer_mechanisms & strict_mechanisms)


def _baseline_mechanisms(answer: str) -> set[str]:
    lower = answer.lower()
    mechanisms: set[str] = set()
    if any(marker in lower for marker in ("fullgc", "full gc", "stop-the-world", "oldgen", "jvm")):
        mechanisms.add("jvm")
    if any(
        marker in lower
        for marker in ("threadpool_busy", "thread pool is full", "线程池", "队列耗尽")
    ):
        mechanisms.add("thread_pool")
    if any(marker in lower for marker in ("sentinel", "flowexception", "限流", "熔断")):
        mechanisms.add("limit")
    if any(marker in lower for marker in ("rs-0095", "幂等", "重复请求", "重复提交")):
        mechanisms.add("idempotency")
    if any(marker in lower for marker in ("metaq", "rocketmq", "topic", "消费组", "队列分配")):
        mechanisms.add("message_queue")
    if any(marker in lower for marker in ("cpu", "request_util")):
        mechanisms.add("cpu")
    if any(marker in lower for marker in ("sql", "tddl", "rds", "数据库", "慢查询", "慢sql")):
        mechanisms.add("database")
    if any(marker in lower for marker in ("tair", "redis", "cache", "缓存")):
        mechanisms.add("cache")
    if any(
        marker in lower
        for marker in (
            "addressnotfound",
            "no provider",
            "找不到服务地址",
            "无服务地址",
            "地址尚未就绪",
        )
    ):
        mechanisms.add("address")
    if any(marker in lower for marker in ("timeout", "超时", "rc=03", "resultcode=03")):
        mechanisms.add("timeout")
    return mechanisms


def _term_mechanisms(term: TrajectoryTerm) -> set[str]:
    lower = term.term.lower()
    mechanisms: set[str] = set()
    if term.kind in SQL_KINDS or any(marker in lower for marker in SQL_EXCEPTION_MARKERS):
        mechanisms.add("database")
    if "sentinel" in lower or "flowexception" in lower or "限流" in lower or "熔断" in lower:
        mechanisms.add("limit")
    if "addressnotfound" in lower or "hsf-0001" in lower or "no provider" in lower:
        mechanisms.add("address")
    if "threadpool" in lower or "rejectedexecution" in lower:
        mechanisms.add("thread_pool")
    if "outofmemory" in lower or "fullgc" in lower or "full gc" in lower:
        mechanisms.add("jvm")
    if "timeoutexception" in lower or "hsf-0002" in lower:
        mechanisms.add("timeout")
    if "datanotexsits" in lower or "tair" in lower or "redis" in lower:
        mechanisms.add("cache")
    return mechanisms


def _root_aligned(baseline: CandidateAnswer, bundle: EvidenceBundle, term: TrajectoryTerm) -> bool:
    baseline_entity_values = _critical_values(baseline)
    if not baseline_entity_values:
        return False
    snippet_text = " ".join(term.snippets).lower()
    if any(value in snippet_text for value in baseline_entity_values):
        return True

    term_lower = term.term.lower()
    for item in bundle.evidence:
        item_text = text_for_features(
            {"name": item.name, "summary": item.summary, "command": item.command}
        ).lower()
        if term_lower in item_text and token_features(item_text) & critical_tokens(baseline):
            return True
    for hypothesis in bundle.hypotheses[:5]:
        hypothesis_text = text_for_features(hypothesis.to_dict()).lower()
        if term_lower in hypothesis_text and token_features(hypothesis_text) & critical_tokens(
            baseline
        ):
            return True
    return False


def _critical_values(baseline: CandidateAnswer) -> list[str]:
    values: list[str] = []
    for token in sorted(critical_tokens(baseline)):
        if token.startswith(("app:", "keyword:")):
            continue
        value = token.split(":", 1)[1].lower()
        if len(value) < 4:
            continue
        values.append(value)
        if "." in value:
            simple = value.rsplit(".", 1)[-1]
            if len(simple) >= 8:
                values.append(simple)
    return values


def _canonical_term_key(term: TrajectoryTerm) -> str:
    value = term.term.lower()
    if term.kind == "exception":
        return value.rsplit(".", 1)[-1]
    return value


def _is_discriminative_exception(term: str) -> bool:
    simple = term.rsplit(".", 1)[-1]
    if simple in GENERIC_EXCEPTION_SIMPLE_NAMES:
        return False
    lower = simple.lower()
    return not any(lower.endswith(suffix) for suffix in WRAPPER_EXCEPTION_SUFFIXES)


def _semantically_covered(answer: str, term: TrajectoryTerm) -> bool:
    text = answer.lower()
    term_text = term.term.lower()
    if any(marker in term_text for marker in ("timeoutexception", "hsf-0002")):
        return "timeout" in text or "超时" in text or "rc=03" in text or "resultcode=03" in text
    if "hsfserviceaddressnotfoundexception" in term_text or "hsf-0001" in term_text:
        return (
            "addressnotfound" in text
            or "no provider" in text
            or "找不到服务地址" in answer
            or "无服务地址" in answer
            or "地址尚未就绪" in answer
        )
    if "sentinelblockexception" in term_text or "flowexception" in term_text:
        return "sentinel" in text or "限流" in answer or "熔断" in answer or "block" in text
    if "communicationsexception" in term_text or "getconnectiontimeoutexception" in term_text:
        return "连接" in answer or "connection" in text or "闪断" in answer
    return False


def _render_evidence_patch(terms: list[TrajectoryTerm]) -> str:
    rendered = [_term_phrase(term) for term in terms]
    if len(rendered) == 1:
        return f"补充证据：相关日志/调用观测中反复出现{rendered[0]}，与上述根因链一致。"
    body = "、".join(rendered[:-1]) + f"以及{rendered[-1]}"
    return f"补充证据：相关日志/调用观测中反复出现{body}，这些信号共同强化上述根因链。"


def _term_phrase(term: TrajectoryTerm) -> str:
    if term.kind == "hsf_code":
        return f"HSF 错误码 {term.term}"
    if term.kind == "tddl_code":
        return f"TDDL 错误码 {term.term}"
    if term.kind == "ora_code":
        return f"Oracle 错误码 {term.term}"
    if term.kind == "sqlstate":
        return f"SQLSTATE {term.term}"
    if term.kind == "rds":
        return f"RDS 实例 {term.term}"
    if term.kind == "exception":
        return f"异常类 {term.term}"
    return term.term


def _append_patch(answer: str, patch: str, *, max_chars: int) -> str:
    compact_answer = answer.rstrip()
    if not compact_answer:
        return patch if len(patch) <= max_chars else ""
    separator = "" if compact_answer.endswith(("。", ".", "；", ";")) else "。"
    enriched = f"{compact_answer}{separator}{patch}"
    if len(enriched) > max_chars:
        return answer
    return re.sub(r"\s+", " ", enriched).strip()
