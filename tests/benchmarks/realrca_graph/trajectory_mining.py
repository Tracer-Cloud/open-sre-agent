from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

EXCEPTION_RE = re.compile(r"\b(?:[a-zA-Z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error)\b")
HSF_CODE_RE = re.compile(r"\bHSF-\d{4}\b", re.IGNORECASE)
TDDL_CODE_RE = re.compile(r"\bTDDL[-_ ]?\d{3,6}\b", re.IGNORECASE)
ORA_CODE_RE = re.compile(r"\bORA-\d{4,5}\b", re.IGNORECASE)
SQLSTATE_RE = re.compile(r"\bSQLSTATE\s*[:=]?\s*[0-9A-Z]{5}\b", re.IGNORECASE)
RDS_RE = re.compile(r"\brm-[0-9a-zA-Z-]{8,}\b")
TRACE_RE = re.compile(r"\b[0-9a-f]{24,40}\b", re.IGNORECASE)
JAVA_SERVICE_RE = re.compile(r"\b(?:com|org|net|io|cn)\.[\w.$]+(?::[\w.-]+)?(?:[@#][\w.$~:-]+)?\b")

STRONG_PHRASES = (
    "THREADPOOL_BUSY",
    "DATANOTEXSITS",
    "SentinelBlockException",
    "FlowException",
    "HSFServiceAddressNotFoundException",
    "HSFTimeOutException",
    "Cannot find target service address",
    "No provider",
    "Provider's HSF thread pool is full",
    "GetConnectionTimeoutException",
    "UnknownHostException",
    "CommunicationsException",
    "SocketTimeoutException",
    "SQLSyntaxErrorException",
    "NumberFormatException",
    "RejectedExecutionException",
    "OutOfMemoryError",
    "Full GC",
    "慢SQL",
    "慢查询",
    "限流",
    "熔断",
    "线程池",
    "缓存穿透",
)
STRONG_PHRASE_RE = re.compile("|".join(re.escape(term) for term in STRONG_PHRASES), re.IGNORECASE)

GENERIC_TERMS = {
    "Exception",
    "Error",
    "RuntimeException",
    "java.lang.RuntimeException",
    "RpcException",
    "HSFException",
    "com.taobao.hsf.exception.HSFException",
    "BizException",
    "SystemException",
    "BIZ_ERROR",
    "TIMEOUT",
    "SUCCESS",
}

BASE_WEIGHTS = {
    "exception": 7,
    "hsf_code": 7,
    "tddl_code": 7,
    "ora_code": 6,
    "sqlstate": 5,
    "strong_phrase": 6,
    "rds": 4,
    "trace": 1,
    "java_service": 2,
}


@dataclass(frozen=True)
class TrajectoryObservation:
    """One visible trajectory text fragment and its provenance tier."""

    source: str
    event: str
    evidence_tier: str
    text: str


@dataclass
class MinedTerm:
    """A high-signal term found in visible trajectories but missing from the answer."""

    term: str
    kind: str
    count: int = 0
    score: int = 0
    graph_supported: bool = False
    source_counts: Counter[str] = field(default_factory=Counter)
    event_counts: Counter[str] = field(default_factory=Counter)
    snippets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "term": self.term,
            "kind": self.kind,
            "count": self.count,
            "score": self.score,
            "graph_supported": self.graph_supported,
            "source_counts": dict(self.source_counts),
            "event_counts": dict(self.event_counts),
            "snippets": list(self.snippets),
        }


def normalize_text(value: str) -> str:
    """Normalize text for case-insensitive containment checks."""

    return re.sub(r"\s+", " ", value.strip()).lower()


def answer_contains(answer: str, term: str) -> bool:
    """Return whether the current answer already covers ``term``."""

    term_norm = normalize_text(term)
    answer_norm = normalize_text(answer)
    if term_norm in answer_norm:
        return True
    simple = term_norm.rsplit(".", 1)[-1]
    return len(simple) > 8 and simple in answer_norm


def compact_snippet(text: str, term: str, *, limit: int = 520) -> str:
    """Return a short redacted snippet around ``term``."""

    redacted = re.sub(
        r"(?i)(token|secret|password|credential|authorization)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    position = redacted.lower().find(term.lower())
    if position < 0:
        position = 0
    window = redacted[max(0, position - 160) : position + limit]
    compact = " ".join(window.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _clean_term(kind: str, term: str) -> str:
    cleaned = term.strip(" \t\r\n\\\"'`,;:()[]{}")
    cleaned = re.sub(r"^(?:n|t)(?=(?:com|org|net|io|cn|java)\.)", "", cleaned)
    if kind == "tddl_code":
        cleaned = re.sub(r"(?i)^TDDL[-_ ]?", "TDDL-", cleaned)
    return cleaned


def extract_terms(text: str) -> list[tuple[str, str]]:
    """Extract RCA-relevant technical terms from one trajectory text blob."""

    terms: list[tuple[str, str]] = []
    patterns = (
        ("exception", EXCEPTION_RE),
        ("hsf_code", HSF_CODE_RE),
        ("tddl_code", TDDL_CODE_RE),
        ("ora_code", ORA_CODE_RE),
        ("sqlstate", SQLSTATE_RE),
        ("strong_phrase", STRONG_PHRASE_RE),
        ("rds", RDS_RE),
        ("trace", TRACE_RE),
        ("java_service", JAVA_SERVICE_RE),
    )
    for kind, regex in patterns:
        for match in regex.finditer(text):
            term = _clean_term(kind, re.sub(r"\s+", " ", match.group(0)))
            if not term or len(term) > 140 or term in GENERIC_TERMS:
                continue
            if kind == "java_service" and "$" in term and "@" not in term and "#" not in term:
                continue
            if kind == "java_service" and term.count(".") >= 5 and "service" not in term.lower():
                continue
            terms.append((kind, term))
    return terms


def _term_score(term: MinedTerm) -> int:
    score = BASE_WEIGHTS.get(term.kind, 1)
    score += min(8, term.count // 4)
    score += min(4, len(term.source_counts))
    if term.graph_supported:
        score += 4
    if term.event_counts.get("agent.tool_result", 0):
        score += 4
    if term.event_counts.get("agent.message", 0):
        score += 1
    if term.kind in {"trace", "java_service"} and not term.graph_supported:
        score -= 3
    return score


def mine_missing_terms(
    *,
    answer: str,
    observations: Iterable[TrajectoryObservation],
    graph_text: str = "",
    limit: int = 8,
    min_score: int = 9,
    snippets_per_term: int = 2,
) -> list[MinedTerm]:
    """Rank visible trajectory terms that are absent from the current answer."""

    graph_norm = normalize_text(graph_text)
    terms: dict[str, MinedTerm] = {}
    for observation in observations:
        for kind, term in extract_terms(observation.text):
            if answer_contains(answer, term):
                continue
            key = f"{kind}:{normalize_text(term)}"
            evidence = terms.setdefault(key, MinedTerm(term=term, kind=kind))
            evidence.count += 1
            evidence.source_counts[observation.source] += 1
            evidence.event_counts[observation.event] += 1
            if normalize_text(term) in graph_norm:
                evidence.graph_supported = True
            if len(evidence.snippets) < snippets_per_term:
                evidence.snippets.append(compact_snippet(observation.text, term))

    ranked = []
    for evidence in terms.values():
        evidence.score = _term_score(evidence)
        if evidence.score >= min_score:
            ranked.append(evidence)
    ranked.sort(key=lambda item: (-item.score, item.kind, item.term))
    return ranked[:limit]
