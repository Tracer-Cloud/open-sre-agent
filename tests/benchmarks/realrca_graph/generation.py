from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from tests.benchmarks.realrca_graph.answer_contract import prompt_contract
from tests.benchmarks.realrca_graph.bundle import bundle_prompt_context
from tests.benchmarks.realrca_graph.causal_paths import build_causal_path_report
from tests.benchmarks.realrca_graph.features import clip_text
from tests.benchmarks.realrca_graph.models import CandidateAnswer, CandidateScore, EvidenceBundle
from tests.benchmarks.realrca_graph.ontology_graph import OntologyGraph

ANSWER_CONTEXT_KINDS = {
    "app",
    "endpoint",
    "evidence_cluster",
    "event",
    "ip",
    "log_error",
    "metric_series",
    "method",
    "service",
    "span",
    "trace",
}
KIND_PRIORITY = {
    "service": 0,
    "method": 1,
    "app": 2,
    "evidence_cluster": 3,
    "metric_series": 4,
    "log_error": 5,
    "span": 6,
    "trace": 7,
    "ip": 8,
    "event": 9,
    "endpoint": 10,
}


@dataclass(frozen=True)
class GenerationCandidateSummary:
    """A compact prior answer summary for the DMA generator prompt."""

    source: str
    graph_support: float
    modality_count: int
    novelty: float
    risk_flags: list[str]
    trace_id: str
    diagnosis_preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenerationCasePackage:
    """Visible case package used to ask DMA for one new candidate answer."""

    case: dict[str, Any]
    baseline: dict[str, str]
    evidence_bundle: dict[str, Any]
    answer_contract: dict[str, Any]
    candidate_summaries: list[GenerationCandidateSummary]
    previous_probe_agents: list[str]
    strategy_hint: str = ""
    matched_system_entities: list[dict[str, Any]] = field(default_factory=list)
    validation_exemplars: list[dict[str, Any]] = field(default_factory=list)
    visible_tool_signals: list[dict[str, Any]] = field(default_factory=list)
    frontier_context: dict[str, Any] = field(default_factory=dict)
    graph_analogues: list[dict[str, Any]] = field(default_factory=list)
    causal_path_hints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "baseline": dict(self.baseline),
            "evidence_bundle": self.evidence_bundle,
            "answer_contract": self.answer_contract,
            "candidate_summaries": [item.to_dict() for item in self.candidate_summaries],
            "previous_probe_agents": list(self.previous_probe_agents),
            "strategy_hint": self.strategy_hint,
            "matched_system_entities": list(self.matched_system_entities),
            "validation_exemplars": list(self.validation_exemplars),
            "visible_tool_signals": list(self.visible_tool_signals),
            "frontier_context": dict(self.frontier_context),
            "graph_analogues": list(self.graph_analogues),
            "causal_path_hints": list(self.causal_path_hints),
        }


def visible_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return only benchmark-visible case fields for prompting."""

    visible = dict(case)
    for key in ("root_cause_chain", "reference", "name"):
        visible.pop(key, None)
    meta = visible.get("meta")
    if isinstance(meta, dict):
        clean_meta = dict(meta)
        clean_meta.pop("name", None)
        visible["meta"] = clean_meta
    return visible


def build_generation_package(
    *,
    case: dict[str, Any],
    baseline: CandidateAnswer,
    bundle: EvidenceBundle,
    candidate_scores: Sequence[tuple[CandidateAnswer, CandidateScore]],
    graph_context: dict[str, Any] | None = None,
    previous_probe_agents: Sequence[str] = (),
    strategy_hint: str = "",
    validation_exemplars: Sequence[dict[str, Any]] = (),
    visible_tool_signals: Sequence[dict[str, Any]] = (),
    frontier_context: dict[str, Any] | None = None,
    graph_analogues: Sequence[dict[str, Any]] = (),
    causal_path_limit: int = 6,
    candidate_limit: int = 5,
    answer_chars: int = 700,
) -> GenerationCasePackage:
    """Build a compact, no-reference prompt package for one candidate-generation run."""

    ranked = sorted(
        candidate_scores,
        key=lambda item: (
            -item[1].graph_support,
            len(item[1].risk_flags),
            -item[1].modality_count,
            item[1].novelty,
            item[0].source,
        ),
    )
    summaries = [
        GenerationCandidateSummary(
            source=answer.source,
            graph_support=score.graph_support,
            modality_count=score.modality_count,
            novelty=score.novelty,
            risk_flags=list(score.risk_flags),
            trace_id=answer.trace_id,
            diagnosis_preview=clip_text(answer.diagnosis_output, answer_chars),
        )
        for answer, score in ranked[:candidate_limit]
    ]
    return GenerationCasePackage(
        case=visible_case(case),
        baseline=baseline.to_result_row(),
        evidence_bundle=_bundle_for_prompt(bundle),
        answer_contract=prompt_contract(bundle),
        candidate_summaries=summaries,
        previous_probe_agents=sorted(set(previous_probe_agents)),
        strategy_hint=strategy_hint.strip(),
        matched_system_entities=_matched_system_entities(graph_context, baseline),
        validation_exemplars=list(validation_exemplars),
        visible_tool_signals=sanitize_visible_tool_signals(visible_tool_signals),
        frontier_context=sanitize_frontier_context(frontier_context or {}),
        graph_analogues=sanitize_graph_analogues(graph_analogues),
        causal_path_hints=_causal_path_hints(graph_context, bundle, limit=causal_path_limit),
    )


def render_generation_prompt(package: GenerationCasePackage) -> str:
    """Render the DMA candidate-generation prompt."""

    payload = json.dumps(_package_for_prompt(package), ensure_ascii=False, indent=2)
    case_id = package.baseline["case_id"]
    trace_id = package.baseline["trace_id"]
    strategy_hint = _strategy_hint_block(package.strategy_hint)
    return textwrap.dedent(
        f"""
        你是 RealRCA-Bench 的 ontology + typed evidence RCA candidate generator。
        你的任务是基于可见 case、当前最高分基线、历史候选摘要、frontier 差分、因果路径提示和证据包，生成一个新的可提交候选答案。

        数据边界：
        - 禁止读取 hidden reference、root_cause_chain、答案文件、提交反馈或任何泄露数据。
        - 本轮只使用下面 JSON package 中的可见字段；不要调用工具，不要要求继续查询。
        - previous_probe_agents 只是历史探针名称，表示这些 case/策略可能已经验证过，不代表答案。
        - public_validation_exemplars 来自公开 validation 集合，只能参考故障模式和诊断写法；
          不要复制其中的业务实体、TraceId、IP、SQL、应用名到当前答案。
        - graph_analogues 来自本地 case graph DB，只能作为结构一致性/风险提示；
          不要复制相似 case 的业务实体、TraceId、IP、SQL、应用名到当前答案。
          analogue_role=negative_constraint 的条目只说明跨机制迁移风险，不能作为改写模板。

        生成策略：
        1. 先判断 current_answer 是否存在 material root error：根因实体错、机制错、传播方向错、或关键证据与结论矛盾。
           若没有 material root error，必须输出 preserve_baseline，并原样保留 current_answer 的 diagnosis_output 和 trace_id。
        2. 若要改写，必须围绕 frontier_differential 与 observations 中共同支持的最强主因实体，写成单一根因链。
           causal_path_hints 中 path_score 高且 risk_flags 少的候选更适合作为 root；只有高 fanout 或 span mentions 连接的候选默认是背景或旁路。
        3. 保留 current_answer 中能被证据支持的服务、接口、异常、SQL、RDS、TraceId 等关键实体；不要为了新颖而丢掉它们。
        4. 证据写法必须面向 SRE：用“告警显示 / Trace 显示 / 指标显示 / 日志显示 / 变更显示”，
           不要出现字段名、评测过程词或中间编号，例如图谱、候选、基线、评测、证据包、h1/h2。
        5. 输出 350-750 中文字，包含主因实体/机制、2-4 条关键证据、传播链、排除项或不确定性、处置建议。
        6. trace_id 默认保留 `{trace_id}`；diagnosis_output 默认只围绕这个主 trace 展开。
           只有 observations 明确支撑另一个真实 trace 且它更能代表主因时才替换，不要追加额外 TraceId 作为旁证。
        7. 若 frontier_differential.blockers 包含 top_hypothesis_negated_by_baseline、known_negative_probe 或 negative_tomography_variant，
           不要复用对应机制；只有 frontier_differential.raw_uncovered_mechanisms 中的未排除机制被至少两类 observations 直接支持时才改写。
        8. 必须满足 answer_contract 中的 required_sections；无法满足时保持 current_answer，不要用猜测补齐。
        9. 最终只输出 JSON，第一个字符是 {{，最后一个字符是 }}。
        {strategy_hint}

        输出 JSON Schema：
        {{
          "case_id": "{case_id}",
          "diagnosis_output": "完整诊断结论",
          "trace_id": "最相关 trace id",
          "strategy": "preserve_baseline | evidence_rewrite | evidence_compression",
          "decision_reason": "一句话说明为什么这样写"
        }}

        JSON package:
        {payload}
        """
    ).strip()


def extract_candidate_result(text: str) -> dict[str, Any] | None:
    """Extract the final JSON object emitted by DMA."""

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _offset = decoder.raw_decode(cleaned[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "diagnosis_output" in value:
            candidates.append(value)
    if candidates:
        return candidates[-1]
    return _extract_relaxed_candidate_object(cleaned)


def _extract_relaxed_candidate_object(text: str) -> dict[str, Any] | None:
    case_id = _relaxed_string_field(text, "case_id")
    trace_id = _relaxed_string_field(text, "trace_id")
    diagnosis_output = _relaxed_multiline_field(text, "diagnosis_output", "trace_id")
    if not case_id or not trace_id or not diagnosis_output:
        return None
    result = {
        "case_id": case_id,
        "diagnosis_output": diagnosis_output,
        "trace_id": trace_id,
    }
    strategy = _relaxed_string_field(text, "strategy")
    decision_reason = _relaxed_string_field(text, "decision_reason")
    if strategy:
        result["strategy"] = strategy
    if decision_reason:
        result["decision_reason"] = decision_reason
    return result


def _relaxed_string_field(text: str, field_name: str) -> str:
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"([^"\n\r]*)"', text)
    if match is None:
        return ""
    return _unescape_relaxed_json_text(match.group(1).strip())


def _relaxed_multiline_field(text: str, field_name: str, next_field_name: str) -> str:
    pattern = (
        rf'"{re.escape(field_name)}"\s*:\s*"'
        rf"(?P<value>.*?)"
        rf'"\s*,\s*"{re.escape(next_field_name)}"\s*:'
    )
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        return ""
    return _unescape_relaxed_json_text(match.group("value").strip())


def _unescape_relaxed_json_text(text: str) -> str:
    return text.replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")


def validate_candidate_result(case_id: str, result: dict[str, Any]) -> str | None:
    """Validate the minimum RealRCA row contract for one generated candidate."""

    for key in ("case_id", "diagnosis_output", "trace_id"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            return f"missing or empty {key}"
    if result["case_id"] != case_id:
        return f"case_id mismatch: {result['case_id']} != {case_id}"
    return None


def candidate_row_from_result(result: dict[str, Any]) -> dict[str, str]:
    """Normalize a generated candidate into the RealRCA result row shape."""

    return {
        "case_id": str(result["case_id"]).strip(),
        "diagnosis_output": str(result["diagnosis_output"]).strip(),
        "trace_id": str(result["trace_id"]).strip(),
    }


def _bundle_for_prompt(bundle: EvidenceBundle) -> dict[str, Any]:
    payload = bundle_prompt_context(bundle, hypothesis_limit=6)
    for hypothesis in payload.get("top_hypotheses", []):
        if not isinstance(hypothesis, dict):
            continue
        for item in hypothesis.get("support", []):
            if isinstance(item, dict):
                item.pop("raw_ref", None)
                item["summary"] = clip_text(item.get("summary", ""), 420)
    return payload


def _package_for_prompt(package: GenerationCasePackage) -> dict[str, Any]:
    return {
        "case": package.case,
        "current_answer": dict(package.baseline),
        "observations": _rename_bundle_fields(package.evidence_bundle),
        "answer_contract": package.answer_contract,
        "prior_answer_summaries": [item.to_dict() for item in package.candidate_summaries],
        "matched_system_entities": list(package.matched_system_entities),
        "public_validation_exemplars": list(package.validation_exemplars),
        "additional_visible_observations": list(package.visible_tool_signals),
        "frontier_differential": dict(package.frontier_context),
        "graph_analogues": list(package.graph_analogues),
        "causal_path_hints": list(package.causal_path_hints),
        "past_probe_names": list(package.previous_probe_agents),
        "strategy_hint": package.strategy_hint,
    }


def sanitize_visible_tool_signals(
    signals: Sequence[dict[str, Any]],
    *,
    limit: int = 8,
    snippet_limit: int = 2,
    snippet_chars: int = 360,
) -> list[dict[str, Any]]:
    """Keep only prompt-safe fields from mined visible tool observations."""

    output: list[dict[str, Any]] = []
    for raw in signals:
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("term") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        if not term or not kind:
            continue
        snippets: list[str] = []
        raw_snippets = raw.get("snippets")
        if raw_snippets is None:
            raw_snippets = [
                item.get("snippet")
                for item in raw.get("occurrences") or []
                if isinstance(item, dict)
            ]
        for snippet in raw_snippets or []:
            if isinstance(snippet, str) and snippet.strip():
                snippets.append(clip_text(snippet, snippet_chars))
            if len(snippets) >= snippet_limit:
                break
        event_counts = {
            str(key): int(value)
            for key, value in (raw.get("event_counts") or {}).items()
            if isinstance(value, int | float)
        }
        tool_result_count = int(
            raw.get("tool_result_count") or event_counts.get("agent.tool_result", 0)
        )
        message_count = int(raw.get("message_count") or event_counts.get("agent.message", 0))
        output.append(
            {
                "term": clip_text(term, 140),
                "kind": clip_text(kind, 32),
                "score": int(raw.get("score") or 0),
                "graph_supported": bool(raw.get("graph_supported")),
                "tool_result_count": tool_result_count,
                "message_count": message_count,
                "snippets": snippets,
            }
        )
        if len(output) >= limit:
            break
    return output


def sanitize_frontier_context(frontier_context: dict[str, Any]) -> dict[str, Any]:
    """Keep only no-path frontier fields that help explain candidate obligations."""

    if not frontier_context:
        return {}
    allowed = {
        "action",
        "analogue_categories",
        "baseline_risks",
        "baseline_support",
        "best_probe_delta",
        "bucket",
        "frontier_score",
        "negative_probe_count",
        "raw_uncovered_mechanisms",
        "signals",
        "blockers",
        "tomography_best_estimate",
        "top_hypothesis",
    }
    output: dict[str, Any] = {}
    for key in allowed:
        value = frontier_context.get(key)
        if isinstance(value, str):
            output[key] = clip_text(value, 360)
        elif isinstance(value, int | float | bool) or value is None:
            output[key] = value
        elif isinstance(value, list):
            output[key] = [clip_text(str(item), 180) for item in value[:10]]
    return output


def sanitize_graph_analogues(
    analogues: Sequence[dict[str, Any]],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Keep only prompt-safe graph analogue structure and remove case identifiers."""

    output: list[dict[str, Any]] = []
    for raw in analogues:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            "case_type",
            "analogue_role",
            "similarity",
            "mechanism_aligned",
            "negative_probe_count",
        ):
            if key not in raw:
                continue
            value = raw.get(key)
            if isinstance(value, str):
                item[key] = clip_text(value, 80)
            elif isinstance(value, int | float | bool) or value is None:
                item[key] = value
        for key in (
            "matched_mechanisms",
            "matched_root_kinds",
            "matched_layers",
            "matched_modalities",
            "matched_edges",
        ):
            value = raw.get(key)
            if isinstance(value, list):
                item[key] = [clip_text(str(entry), 120) for entry in value[:8]]
        root_patterns = raw.get("root_patterns")
        if isinstance(root_patterns, list):
            item["root_patterns"] = [
                _redact_analogue_entity_text(str(entry)) for entry in root_patterns[:3]
            ]
        if item:
            output.append(item)
        if len(output) >= limit:
            break
    return output


def _causal_path_hints(
    graph_context: dict[str, Any] | None,
    bundle: EvidenceBundle,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    if not graph_context:
        return []
    report = build_causal_path_report(graph_context, bundle, max_depth=5, seed_limit=8)
    output: list[dict[str, Any]] = []
    for item in report.hypotheses[:limit]:
        path_nodes = [
            {
                "kind": node.kind,
                "label": clip_text(node.label, 120),
            }
            for node in item.path_nodes[:6]
        ]
        output.append(
            {
                "root_option": clip_text(item.hypothesis_label, 180),
                "kind": clip_text(item.hypothesis_kind, 60),
                "root_layer": clip_text(item.root_layer, 60),
                "hypothesis_score": item.hypothesis_score,
                "path_score": item.path_score,
                "path_length": item.path_length,
                "risk_flags": [clip_text(flag, 80) for flag in item.risk_flags[:6]],
                "path_nodes": path_nodes,
            }
        )
    return output


def _redact_analogue_entity_text(text: str) -> str:
    redacted = re.sub(r"\b[0-9a-f]{24,40}\b", "[trace]", text, flags=re.I)
    redacted = re.sub(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b", "[ip]", redacted
    )
    redacted = re.sub(r"\brm-[0-9a-zA-Z-]+\b", "[rds]", redacted)
    return clip_text(redacted, 180)


def _rename_bundle_fields(payload: dict[str, Any]) -> dict[str, Any]:
    renamed = dict(payload)
    hypotheses = renamed.pop("top_hypotheses", [])
    root_options: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        item = dict(hypothesis)
        item.pop("id", None)
        support_rows: list[dict[str, Any]] = []
        for support in item.get("support", []):
            if isinstance(support, dict):
                support_item = dict(support)
                support_item.pop("id", None)
                support_rows.append(support_item)
        item["support"] = support_rows
        root_options.append(item)
    renamed["possible_root_causes"] = root_options
    return renamed


def _strategy_hint_block(strategy_hint: str) -> str:
    if not strategy_hint.strip():
        return ""
    return (
        "\n        本轮额外策略约束：\n"
        f"        - {strategy_hint.strip()}\n"
        "        - 若该策略与可见证据冲突，必须说明不采用，并回到证据最强的单一根因。"
    )


def _matched_system_entities(
    graph_context: dict[str, Any] | None,
    baseline: CandidateAnswer,
    *,
    limit: int = 6,
    neighbor_limit: int = 8,
) -> list[dict[str, Any]]:
    if not graph_context:
        return []
    graph = OntologyGraph.from_context(graph_context)
    hits = graph.node_hits_for_text(
        {"diagnosis": baseline.diagnosis_output, "trace_id": baseline.trace_id},
        kinds=ANSWER_CONTEXT_KINDS,
        limit=limit * 3,
    )
    hits.sort(
        key=lambda item: (
            -item.overlap,
            KIND_PRIORITY.get(item.node.kind, 99),
            item.node.label,
        )
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.node.id in seen:
            continue
        seen.add(hit.node.id)
        neighbors: list[dict[str, str]] = []
        for edge in graph.incident_edges(hit.node.id)[:neighbor_limit]:
            if edge.source == hit.node.id:
                neighbor_id = edge.target
                direction = "out"
            else:
                neighbor_id = edge.source
                direction = "in"
            neighbor = graph.nodes.get(neighbor_id)
            if neighbor is None:
                continue
            neighbors.append(
                {
                    "direction": direction,
                    "rel": edge.rel,
                    "kind": neighbor.kind,
                    "label": clip_text(neighbor.label, 120),
                }
            )
        output.append(
            {
                "kind": hit.node.kind,
                "label": clip_text(hit.node.label, 160),
                "overlap": hit.overlap,
                "neighbors": neighbors,
            }
        )
        if len(output) >= limit:
            break
    return output
