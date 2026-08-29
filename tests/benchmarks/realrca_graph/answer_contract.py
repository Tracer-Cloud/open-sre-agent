from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text, token_features
from tests.benchmarks.realrca_graph.models import CandidateAnswer, EvidenceBundle

ROOT_RE = re.compile(r"根因|root\s*cause|定位", re.I)
MECHANISM_RE = re.compile(
    r"导致|引发|触发|因为|由于|超时|限流|慢\s*SQL|慢查询|线程池|连接池|"
    r"Full\s*GC|OOM|堆积|热点|抖动|失败|异常|timeout|timed\s*out|"
    r"throttl|fail(?:ed|ure)?|exception",
    re.I,
)
EVIDENCE_RE = re.compile(r"关键证据|证据|告警|Trace|指标|日志|SLS|SQL|变更|事件", re.I)
CHAIN_RE = re.compile(
    r"影响链路|传播链|调用链|最终|进而|→|->|导致|触发|caus(?:e|ed|ing)|downstream|upstream", re.I
)
EXCLUSION_RE = re.compile(r"排除|不是|并非|未发现|无证据|不支持|而非|不是.*导致", re.I)
ACTION_RE = re.compile(r"处置建议|建议|优先|恢复|回滚|扩容|限流|降级|摘除|重启|排查|确认", re.I)

MODALITY_WORDS = {
    "alarm": ("告警", "alarm"),
    "trace": ("trace", "调用链", "span"),
    "metric": ("指标", "metric", "qps", "rt", "成功率"),
    "log": ("日志", "sls", "log", "exception", "异常"),
    "sql": ("sql", "tddl", "rds", "慢sql", "慢查询", "表"),
    "event": ("事件", "变更", "发布", "重启", "event", "change", "deploy"),
    "topology": ("调用方向", "上游", "下游", "拓扑", "链路"),
}

SECTION_WEIGHTS = {
    "root_statement": 0.18,
    "mechanism": 0.14,
    "observable_evidence": 0.18,
    "causal_chain": 0.14,
    "exclusion_or_uncertainty": 0.1,
    "remediation": 0.08,
    "top_root_alignment": 0.1,
    "evidence_modality_coverage": 0.08,
}


@dataclass(frozen=True)
class AnswerContractAssessment:
    """Structure and evidence-grounding assessment for one RCA answer."""

    case_id: str
    score: float
    covered_sections: list[str]
    missing_sections: list[str]
    mentioned_modalities: list[str]
    expected_modalities: list[str]
    root_overlap_count: int
    top_root_label: str
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_answer_contract(
    answer: CandidateAnswer,
    bundle: EvidenceBundle,
    *,
    min_modalities: int = 2,
) -> AnswerContractAssessment:
    """Check whether an answer reads like a grounded RCA, not only a token match."""

    text = answer.diagnosis_output
    sections = {
        "root_statement": bool(ROOT_RE.search(text)),
        "mechanism": bool(MECHANISM_RE.search(text)),
        "observable_evidence": bool(EVIDENCE_RE.search(text)),
        "causal_chain": bool(CHAIN_RE.search(text)),
        "exclusion_or_uncertainty": bool(EXCLUSION_RE.search(text)),
        "remediation": bool(ACTION_RE.search(text)),
    }
    top = bundle.hypotheses[0] if bundle.hypotheses else None
    answer_tokens = token_features(text)
    root_tokens = token_features(top.to_dict()) if top is not None else set()
    root_overlap = answer_tokens & root_tokens
    sections["top_root_alignment"] = top is None or len(root_overlap) >= 2

    expected_modalities = _expected_modalities(bundle)
    mentioned_modalities = _mentioned_modalities(text, expected_modalities)
    required_modality_count = min(min_modalities, len(expected_modalities))
    sections["evidence_modality_coverage"] = len(mentioned_modalities) >= required_modality_count

    covered = [name for name, ok in sections.items() if ok]
    missing = [name for name, ok in sections.items() if not ok]
    score = round(sum(SECTION_WEIGHTS[name] for name in covered), 4)
    flags = _flags(
        text=text,
        sections=sections,
        score=score,
        mentioned_modalities=mentioned_modalities,
        required_modality_count=required_modality_count,
    )
    return AnswerContractAssessment(
        case_id=answer.case_id,
        score=score,
        covered_sections=covered,
        missing_sections=missing,
        mentioned_modalities=mentioned_modalities,
        expected_modalities=expected_modalities,
        root_overlap_count=len(root_overlap),
        top_root_label=top.label if top is not None else "",
        flags=flags,
    )


def prompt_contract(bundle: EvidenceBundle) -> dict[str, Any]:
    """Return compact answer-contract guidance for the generator prompt."""

    top_roots = [
        {
            "label": item.label,
            "layer": item.root_layer,
            "modalities": list(item.modalities),
            "reason": clip_text(item.reason, 220),
        }
        for item in bundle.hypotheses[:3]
    ]
    return {
        "required_sections": [
            "single concrete root cause sentence",
            "mechanism that explains why the root caused the symptom",
            "2-4 observable evidence points tied to trace/metric/log/sql/event data",
            "causal impact chain from root to alarm symptom",
            "explicit exclusion or uncertainty for plausible alternatives",
            "operator-facing remediation suggestion",
        ],
        "expected_modalities": _expected_modalities(bundle),
        "top_root_options": top_roots,
        "style_constraints": [
            "preserve the current answer when it already satisfies the evidence",
            "prefer exact service, method, exception, SQL table, RDS, IP, and trace identifiers",
            "do not mention benchmark, graph, candidate, hypothesis, or evidence-bundle process terms",
        ],
    }


def _expected_modalities(bundle: EvidenceBundle) -> list[str]:
    modalities: list[str] = []
    for hypothesis in bundle.hypotheses[:3]:
        for modality in hypothesis.modalities:
            if modality not in modalities and modality != "other":
                modalities.append(modality)
    if modalities:
        return modalities
    for evidence in bundle.evidence:
        if evidence.modality not in modalities and evidence.modality != "other":
            modalities.append(evidence.modality)
    return modalities


def _mentioned_modalities(text: str, expected_modalities: list[str]) -> list[str]:
    lower = text.lower()
    output = []
    for modality in expected_modalities:
        if any(word.lower() in lower for word in MODALITY_WORDS.get(modality, (modality,))):
            output.append(modality)
    return output


def _flags(
    *,
    text: str,
    sections: dict[str, bool],
    score: float,
    mentioned_modalities: list[str],
    required_modality_count: int,
) -> list[str]:
    flags: list[str] = []
    if len(text.strip()) < 180:
        flags.append("contract_answer_too_short")
    if len(text) > 2200:
        flags.append("contract_answer_too_long")
    if not sections["root_statement"]:
        flags.append("contract_missing_root_statement")
    if not sections["mechanism"]:
        flags.append("contract_missing_mechanism")
    if not sections["observable_evidence"]:
        flags.append("contract_missing_observable_evidence")
    if not sections["causal_chain"]:
        flags.append("contract_missing_causal_chain")
    if not sections["top_root_alignment"]:
        flags.append("contract_low_top_root_alignment")
    if len(mentioned_modalities) < required_modality_count:
        flags.append("contract_low_evidence_modality_coverage")
    if score < 0.62:
        flags.append("contract_incomplete_answer")
    return flags
