from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.case_analogues import (
    KIND_MECHANISMS,
    MECHANISM_LAYERS,
)
from tests.benchmarks.realrca_graph.features import (
    clip_text,
    infer_modality,
    infer_root_layer,
    keyword_features,
    token_features,
)
from tests.benchmarks.realrca_graph.graph_store import DEFAULT_GRAPH_DB
from tests.benchmarks.realrca_graph.io import load_json
from tests.benchmarks.realrca_graph.probe_feedback import ProbeFeedbackLedger

ENTITY_PREFIXES = (
    "app:",
    "service:",
    "method:",
    "exception:",
    "rds:",
    "sql_table:",
    "sql_id:",
    "ip:",
)
HIGH_SIGNAL_ROOT_KINDS = {
    "app_log_limit",
    "app_sql_error",
    "auth_session_failure",
    "business_system_error",
    "connection_pool_exhausted",
    "custom_monitor_signal",
    "db_access_failure",
    "external_dependency_failure",
    "heavy_business_query",
    "hsf_threadpool_busy",
    "metaq_broker_failure",
    "metaq_business_failure",
    "metaq_duplicate_update_conflict",
    "pod_runtime_event",
    "rds_sql_detail",
    "rds_sql_stat",
    "sql_log_error",
}
NOISY_ENTITY_FRAGMENTS = (
    "app:app-has_",
    "app:case-has_",
    "app:center-zb",
    "app:cluster-filter",
    "app:cluster-router",
    "app:endpoint-calls",
    "app:evidence-cluster",
    "app:mapping-type",
    "app:multi-signal",
    "app:register-mode",
    "app:registry-cluster-type",
    "app:span-client",
    "app:trace-has_",
)
EXTRA_KIND_MECHANISMS = {
    "auth_session_failure": ("auth",),
    "custom_monitor_signal": ("business_metric",),
    "metaq_duplicate_update_conflict": ("consume_failure", "mq_duplicate_conflict"),
    "pattern_auth_session_failure": ("auth",),
    "pattern_metaq_duplicate_update_conflict": ("consume_failure", "mq_duplicate_conflict"),
}
PROFILE_MECHANISM_ALIASES: dict[str, tuple[str, ...]] = {
    "auth": (
        "http_401",
        "unauthorized",
        "login_for_sunfire",
        "buc_sso",
        "buc auth",
        "sso login",
        "sso token",
    ),
    "business_metric": (
        "custom_monitor",
        "custom_monitor_signal",
        "spm_",
        "业务指标",
        "失败数",
        "成功率",
    ),
    "change": (
        "aone",
        "changefree",
        "config_push",
        "diamond",
        "normandy",
        "offline_host",
        "publish_no_qualification",
    ),
    "consume_failure": (
        "biz_error",
        "business_error",
        "business consume failure",
        "mqrecv",
        "duplicate_update_conflict",
        "notify@recv",
        "payment_failure",
        "未查询到",
    ),
    "mq_duplicate_conflict": (
        "duplicate_update_conflict",
        "update_error",
        "updatewithversion",
        "optimistic lock",
        "version conflict",
        "重复消费",
        "重投",
        "幂等",
        "乐观锁",
        "更新失败",
    ),
    "data_quality": (
        "accountbalance",
        "balance",
        "biz_error",
        "business_system_error",
        "duplicate entry",
        "jsonexception",
        "payment_failure",
        "parse systeminfo",
        "system_error",
        "账户余额不足",
        "电子面单",
        "余额不足",
        "资格",
    ),
    "host": (
        "_none_core_host",
        "_offline_host",
        "doom_host",
        "graphdiskiocheck",
        "single-host",
        "target-host",
    ),
    "limit": (
        "sentinel_block",
        "sentinelblockexception",
        "ump_sentinel_block",
        "限流",
    ),
    "memory": (
        "full gc",
        "fullgc",
        "jvm_gc",
        "jvm_memory",
        "outofmemory",
    ),
    "mq": (
        "broker_connectivity_failure",
        "consumemessagethread",
        "metaq_receive_qps",
        "middleware_metaq",
        "mqrecv",
        "nameserver",
        "rocketmq",
    ),
    "network": (
        "addressnotfound",
        "connection refused",
        "connection reset",
        "connection timed out",
        "no route to host",
    ),
    "pod": (
        "evict",
        "oomkilled",
        "pod_runtime_event",
    ),
    "provider_error_qps": (
        "hsf provider success_rate",
        "middleware_hsf_provider_service_method_error_qps",
        "provider error_qps",
        "provider_error_qps_spike",
    ),
    "provider_rpc_error": (
        "provider_subset_rpc_error",
        "rpc_error",
        "rpc_err",
    ),
    "repeated_query": (
        "n+1",
        "repeated_query_fanout",
        "repeat_count",
        "sql fanout",
        "sql_tables=",
    ),
    "security": (
        "fastjson payload",
        "fastjson rce",
        "fourier_check",
        "heimdall",
        "malicious",
        "security-fourier",
        "ssrf",
        "x5action",
        "安全扫描",
        "恶意",
        "路径穿越",
    ),
    "sql": (
        "diagnose_rds_sql",
        "middleware_tddl_write_rt",
        "slow sql",
        "slowqueries",
        "sql_id",
        "sql_table",
        "tddl_query@",
        "tddl_write_rt",
        "慢sql",
    ),
    "thread_pool": (
        "hsf-thread",
        "provider threadpool",
        "thread pool is full",
        "threadpool_busy",
        "线程池",
    ),
    "traffic_source": (
        "middleware_tddl_read_qps",
        "principal_app_instance",
        "read_qps_traffic_source",
        "流量来源",
    ),
    "timeout": (
        "hsftimeoutexception",
        "timed out",
        "timeout",
        "超时",
    ),
}
EXTRA_MECHANISM_LAYERS = {
    "auth": ("service_dependency",),
}
PROFILE_SIGNAL_MARKERS = frozenset(
    needle for needles in PROFILE_MECHANISM_ALIASES.values() for needle in needles
)
NON_CAUSAL_PROFILE_EVIDENCE_NAMES = {
    "app_get",
    "app_resources",
    "sls_store_list",
}


@dataclass(frozen=True)
class GraphCaseProfile:
    """One case-level profile assembled from indexed graph database rows."""

    graph_label: str
    split: str
    case_id: str
    case_type: str
    root_kinds: list[str]
    root_layers: list[str]
    mechanisms: list[str]
    modalities: list[str]
    entities: list[str]
    edge_fingerprints: list[str]
    root_labels: list[str]
    evidence_preview: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphAnalogueMatch:
    """One structurally similar indexed case graph."""

    split: str
    graph_label: str
    case_id: str
    case_type: str
    similarity: float
    mechanism_score: float
    root_kind_score: float
    layer_score: float
    modality_score: float
    entity_score: float
    edge_score: float
    matched_mechanisms: list[str]
    matched_root_kinds: list[str]
    matched_layers: list[str]
    matched_modalities: list[str]
    matched_entities: list[str]
    matched_edges: list[str]
    root_labels: list[str]
    evidence_preview: list[str]
    negative_probe_count: int
    best_probe_accuracy: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphAnalogueCase:
    """Analogue matches for one query case graph."""

    case_id: str
    case_suffix: str
    graph_label: str
    case_type: str
    profile: GraphCaseProfile
    matches: list[GraphAnalogueMatch]
    categories: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_suffix": self.case_suffix,
            "graph_label": self.graph_label,
            "case_type": self.case_type,
            "profile": self.profile.to_dict(),
            "matches": [item.to_dict() for item in self.matches],
            "categories": list(self.categories),
        }


@dataclass(frozen=True)
class GraphAnalogueReport:
    """Cross-case graph analogue report generated from the indexed graph DB."""

    db_path: str
    split: str
    query_graph_label: str
    search_graph_labels: list[str]
    search_splits: list[str]
    case_count: int
    category_counts: dict[str, int]
    cases: list[GraphAnalogueCase]

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "split": self.split,
            "query_graph_label": self.query_graph_label,
            "search_graph_labels": list(self.search_graph_labels),
            "search_splits": list(self.search_splits),
            "hidden_test_reference_used": False,
            "case_count": self.case_count,
            "category_counts": dict(self.category_counts),
            "cases": [item.to_dict() for item in self.cases],
        }


def build_graph_analogue_report(
    *,
    db_path: Path = DEFAULT_GRAPH_DB,
    split: str = "test",
    query_graph_label: str,
    search_graph_labels: Sequence[str] = (),
    search_splits: Sequence[str] = (),
    case_ids: Sequence[str] = (),
    match_limit: int = 5,
    leaderboard_path: Path | None = None,
    team_name: str = "隐元玩一玩",
) -> GraphAnalogueReport:
    """Find structurally similar case graphs without reading hidden references."""

    wanted = {item.lower() for item in case_ids}
    labels = list(search_graph_labels) or [query_graph_label]
    ledger = _feedback_ledger(leaderboard_path, team_name)
    query_profiles = load_graph_case_profiles(
        db_path=db_path,
        split=split,
        graph_labels=[query_graph_label],
    )
    query_profiles = [
        profile
        for profile in query_profiles
        if not wanted
        or profile.case_id.lower() in wanted
        or _case_suffix(profile.case_id) in wanted
    ]
    resolved_search_splits = list(search_splits) or [split]
    search_profiles: list[GraphCaseProfile] = []
    for search_split in resolved_search_splits:
        search_profiles.extend(
            load_graph_case_profiles(
                db_path=db_path,
                split=search_split,
                graph_labels=labels,
            )
        )
    cases: list[GraphAnalogueCase] = []
    for profile in query_profiles:
        matches = _match_profile(
            profile,
            search_profiles,
            match_limit=match_limit,
            ledger=ledger,
        )
        categories = _categories(profile, matches)
        cases.append(
            GraphAnalogueCase(
                case_id=profile.case_id,
                case_suffix=_case_suffix(profile.case_id),
                graph_label=profile.graph_label,
                case_type=profile.case_type,
                profile=profile,
                matches=matches,
                categories=categories,
            )
        )
    cases.sort(key=lambda item: (-_top_similarity(item), item.case_type, item.case_id))
    return GraphAnalogueReport(
        db_path=str(db_path),
        split=split,
        query_graph_label=query_graph_label,
        search_graph_labels=list(labels),
        search_splits=list(resolved_search_splits),
        case_count=len(cases),
        category_counts=dict(Counter(category for item in cases for category in item.categories)),
        cases=cases,
    )


def load_graph_case_profiles(
    *,
    db_path: Path = DEFAULT_GRAPH_DB,
    split: str = "test",
    graph_labels: Sequence[str] = (),
) -> list[GraphCaseProfile]:
    """Load compact case profiles from indexed graph rows."""

    label_filter = set(graph_labels)
    with sqlite3.connect(db_path) as conn:
        cases = _case_rows(conn, split=split, graph_labels=label_filter)
        roots = _group_rows(
            conn,
            "SELECT graph_label, case_id, rank, kind, label, reason, props_json "
            "FROM root_candidates WHERE split = ?",
            split=split,
            graph_labels=label_filter,
            order_by="score DESC, rank ASC",
        )
        evidence = _group_rows(
            conn,
            "SELECT graph_label, case_id, name, command, summary FROM evidence WHERE split = ?",
            split=split,
            graph_labels=label_filter,
            order_by="name ASC",
        )
        nodes = _group_rows(
            conn,
            "SELECT graph_label, case_id, node_id, kind, label, props_json FROM nodes WHERE split = ?",
            split=split,
            graph_labels=label_filter,
            order_by="kind ASC, label ASC",
        )
        edges = _group_rows(
            conn,
            "SELECT graph_label, case_id, source, rel, target FROM edges WHERE split = ?",
            split=split,
            graph_labels=label_filter,
            order_by="rel ASC, source ASC, target ASC",
        )

    profiles: list[GraphCaseProfile] = []
    for row in cases:
        key = (row["graph_label"], row["case_id"])
        profiles.append(
            _profile_from_rows(
                case=row,
                roots=roots.get(key, []),
                evidence=evidence.get(key, []),
                nodes=nodes.get(key, []),
                edges=edges.get(key, []),
            )
        )
    return profiles


def render_graph_analogue_markdown(report: GraphAnalogueReport, *, limit: int = 60) -> str:
    """Render the graph analogue report for RCA experiment planning."""

    lines = [
        "# RealRCA Graph Analogues",
        "",
        f"- db: `{report.db_path}`",
        f"- split: `{report.split}`",
        f"- query_graph_label: `{report.query_graph_label}`",
        f"- search_graph_labels: `{report.search_graph_labels}`",
        f"- search_splits: `{report.search_splits}`",
        "- hidden_test_reference_used: `False`",
        f"- cases: `{report.case_count}`",
        f"- top_categories: `{_top_counts(report.category_counts)}`",
        "",
        "## Ranked Cases",
        "",
        "| rank | case | type | top_similarity | analogue | mechanisms | root_kinds | categories |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(report.cases[:limit], start=1):
        match = item.matches[0] if item.matches else None
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{item.case_suffix}`",
                    item.case_type or "-",
                    f"{match.similarity:.4f}" if match is not None else "0.0000",
                    f"`{match.split}:{_case_suffix(match.case_id)}`" if match is not None else "-",
                    ",".join(match.matched_mechanisms[:4]) if match is not None else "-",
                    ",".join(match.matched_root_kinds[:3]) if match is not None else "-",
                    ",".join(item.categories[:4]) or "-",
                ]
            )
            + " |"
        )
    lines.extend(["", "## Case Notes", ""])
    for item in report.cases[:limit]:
        lines.extend(
            [
                f"### `{item.case_suffix}` {item.case_type}",
                "",
                f"- case_id: `{item.case_id}`",
                f"- graph_label: `{item.graph_label}`",
                f"- profile_mechanisms: `{item.profile.mechanisms}`",
                f"- profile_root_kinds: `{item.profile.root_kinds}`",
                f"- profile_layers: `{item.profile.root_layers}`",
                f"- profile_edges: `{item.profile.edge_fingerprints[:8]}`",
                f"- categories: `{item.categories}`",
                "",
            ]
        )
        for match in item.matches:
            lines.extend(
                [
                    (
                        f"- analogue `{match.case_id}` split=`{match.split}` label=`{match.graph_label}` "
                        f"similarity=`{match.similarity}` mechanism=`{match.mechanism_score}` "
                        f"root_kind=`{match.root_kind_score}` layer=`{match.layer_score}` "
                        f"modality=`{match.modality_score}` entity=`{match.entity_score}` "
                        f"edge=`{match.edge_score}` negative_probes=`{match.negative_probe_count}`"
                    ),
                    f"  matched_mechanisms=`{match.matched_mechanisms}`",
                    f"  matched_root_kinds=`{match.matched_root_kinds}`",
                    f"  matched_layers=`{match.matched_layers}`",
                    f"  matched_modalities=`{match.matched_modalities}`",
                    f"  matched_entities=`{match.matched_entities[:10]}`",
                    f"  matched_edges=`{match.matched_edges[:8]}`",
                    f"  root_labels=`{match.root_labels}`",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def graph_analogues_for_prompt(
    report: GraphAnalogueReport, *, limit: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """Return prompt-safe graph analogue snippets keyed by case_id."""

    output: dict[str, list[dict[str, Any]]] = {}
    for case in report.cases:
        snippets: list[dict[str, Any]] = []
        for match in case.matches[:limit]:
            snippets.append(_prompt_snippet(match.to_dict()))
        output[case.case_id] = snippets
    return output


def graph_analogues_for_prompt_payload(
    payload: dict[str, Any],
    *,
    limit: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Return prompt-safe graph analogue snippets from a serialized report."""

    output: dict[str, list[dict[str, Any]]] = {}
    for case in payload.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or "")
        if not case_id:
            continue
        snippets = [
            _prompt_snippet(match)
            for match in case.get("matches", [])[:limit]
            if isinstance(match, dict)
        ]
        output[case_id] = snippets
    return output


def _prompt_snippet(match: dict[str, Any]) -> dict[str, Any]:
    matched_mechanisms = list(match.get("matched_mechanisms") or [])[:6]
    return {
        "case_type": match.get("case_type"),
        "similarity": match.get("similarity"),
        "analogue_role": "supporting_analogue" if matched_mechanisms else "negative_constraint",
        "mechanism_aligned": bool(matched_mechanisms),
        "matched_mechanisms": matched_mechanisms,
        "matched_root_kinds": list(match.get("matched_root_kinds") or [])[:4],
        "matched_layers": list(match.get("matched_layers") or [])[:4],
        "matched_modalities": list(match.get("matched_modalities") or [])[:5],
        "matched_entities": list(match.get("matched_entities") or [])[:8],
        "matched_edges": list(match.get("matched_edges") or [])[:6],
        "root_patterns": [
            clip_text(label, 160) for label in list(match.get("root_labels") or [])[:3]
        ],
        "negative_probe_count": match.get("negative_probe_count", 0),
    }


def _case_rows(
    conn: sqlite3.Connection,
    *,
    split: str,
    graph_labels: set[str],
) -> list[dict[str, str]]:
    query = (
        "SELECT graph_label, split, case_id, case_type, retrieval_summary "
        "FROM cases WHERE split = ?"
    )
    params: list[Any] = [split]
    if graph_labels:
        query += f" AND graph_label IN ({','.join('?' for _ in graph_labels)})"
        params.extend(sorted(graph_labels))
    query += " ORDER BY graph_label, case_id"
    return [
        {
            "graph_label": str(row[0]),
            "split": str(row[1]),
            "case_id": str(row[2]),
            "case_type": str(row[3] or ""),
            "retrieval_summary": str(row[4] or ""),
        }
        for row in conn.execute(query, params).fetchall()
    ]


def _group_rows(
    conn: sqlite3.Connection,
    query: str,
    *,
    split: str,
    graph_labels: set[str],
    order_by: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    params: list[Any] = [split]
    where = ""
    if graph_labels:
        where = f" AND graph_label IN ({','.join('?' for _ in graph_labels)})"
        params.extend(sorted(graph_labels))
    rows = conn.execute(
        f"{query}{where} ORDER BY graph_label, case_id, {order_by}", params
    ).fetchall()
    output: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        graph_label = str(row[0])
        case_id = str(row[1])
        output[(graph_label, case_id)].append(_row_payload(row))
    return output


def _row_payload(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {f"c{index}": value for index, value in enumerate(row)}


def _profile_from_rows(
    *,
    case: dict[str, str],
    roots: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> GraphCaseProfile:
    root_payload = [
        {
            "rank": item.get("c2"),
            "kind": str(item.get("c3") or ""),
            "label": str(item.get("c4") or ""),
            "reason": str(item.get("c5") or ""),
            "props": _json_obj(item.get("c6")),
        }
        for item in roots[:8]
    ]
    evidence_payload = [
        {
            "name": str(item.get("c2") or ""),
            "command": str(item.get("c3") or ""),
            "summary": str(item.get("c4") or ""),
        }
        for item in evidence[:32]
    ]
    node_payload = [
        {
            "node_id": str(item.get("c2") or ""),
            "kind": str(item.get("c3") or ""),
            "label": str(item.get("c4") or ""),
            "props": _json_obj(item.get("c5")),
        }
        for item in nodes[:80]
    ]
    node_kinds = {item["node_id"]: item["kind"] for item in node_payload}
    edge_fingerprints = sorted(
        {
            _edge_fingerprint(
                source_kind=node_kinds.get(
                    str(item.get("c2") or ""), _node_kind_from_id(str(item.get("c2") or ""))
                ),
                rel=str(item.get("c3") or ""),
                target_kind=node_kinds.get(
                    str(item.get("c4") or ""), _node_kind_from_id(str(item.get("c4") or ""))
                ),
            )
            for item in edges[:160]
        }
    )
    root_kinds = sorted({item["kind"] for item in root_payload if item["kind"]})
    mechanisms: set[str] = set()
    for kind in root_kinds:
        mechanisms.update(KIND_MECHANISMS.get(kind, ()))
        mechanisms.update(EXTRA_KIND_MECHANISMS.get(kind, ()))
    for item in root_payload:
        if not _is_high_signal_root_kind(item["kind"]):
            continue
        mechanisms.update(
            keyword_features(
                json.dumps(
                    {"kind": item["kind"], "label": item["label"], "reason": item["reason"]},
                    ensure_ascii=False,
                )
            )
        )
    mechanisms.update(_mechanisms_from_profile_payload(root_payload, evidence_payload))
    root_layers = {
        infer_root_layer(item["kind"], item["label"], item["props"], item["reason"])
        for item in root_payload
        if item["kind"] or item["label"]
    }
    for mechanism in mechanisms:
        root_layers.update(MECHANISM_LAYERS.get(mechanism, ()))
        root_layers.update(EXTRA_MECHANISM_LAYERS.get(mechanism, ()))
    modalities = {
        infer_modality(item["name"], item["command"], item["summary"]) for item in evidence_payload
    }
    modalities.discard("other")
    tokens = token_features(
        {
            "roots": root_payload,
            "evidence": evidence_payload,
            "nodes": node_payload,
            "retrieval_summary": case.get("retrieval_summary", ""),
        }
    )
    return GraphCaseProfile(
        graph_label=case["graph_label"],
        split=case["split"],
        case_id=case["case_id"],
        case_type=case["case_type"],
        root_kinds=root_kinds,
        root_layers=sorted(root_layers),
        mechanisms=sorted(mechanisms),
        modalities=sorted(modalities),
        entities=sorted(_entity_tokens(tokens))[:80],
        edge_fingerprints=edge_fingerprints[:60],
        root_labels=[clip_text(item["label"], 180) for item in root_payload[:5]],
        evidence_preview=[clip_text(item["summary"], 180) for item in evidence_payload[:6]],
    )


def _match_profile(
    query: GraphCaseProfile,
    candidates: Sequence[GraphCaseProfile],
    *,
    match_limit: int,
    ledger: ProbeFeedbackLedger | None,
) -> list[GraphAnalogueMatch]:
    matches: list[GraphAnalogueMatch] = []
    for candidate in candidates:
        if (
            candidate.split == query.split
            and candidate.graph_label == query.graph_label
            and candidate.case_id == query.case_id
        ):
            continue
        scores = _similarity(query, candidate)
        if scores["similarity"] <= 0:
            continue
        feedback = ledger.for_case_id(candidate.case_id) if ledger is not None else None
        matches.append(
            GraphAnalogueMatch(
                split=candidate.split,
                graph_label=candidate.graph_label,
                case_id=candidate.case_id,
                case_type=candidate.case_type,
                similarity=scores["similarity"],
                mechanism_score=scores["mechanism"],
                root_kind_score=scores["root_kind"],
                layer_score=scores["layer"],
                modality_score=scores["modality"],
                entity_score=scores["entity"],
                edge_score=scores["edge"],
                matched_mechanisms=_overlap(query.mechanisms, candidate.mechanisms),
                matched_root_kinds=_overlap(query.root_kinds, candidate.root_kinds),
                matched_layers=_overlap(query.root_layers, candidate.root_layers),
                matched_modalities=_overlap(query.modalities, candidate.modalities),
                matched_entities=_overlap(query.entities, candidate.entities),
                matched_edges=_overlap(query.edge_fingerprints, candidate.edge_fingerprints),
                root_labels=list(candidate.root_labels),
                evidence_preview=list(candidate.evidence_preview),
                negative_probe_count=feedback.negative_count if feedback is not None else 0,
                best_probe_accuracy=_best_probe_accuracy(feedback),
            )
        )
    matches.sort(
        key=lambda item: (
            -item.similarity,
            -item.mechanism_score,
            -item.root_kind_score,
            -item.entity_score,
            item.graph_label,
            item.case_id,
        )
    )
    return matches[:match_limit]


def _similarity(left: GraphCaseProfile, right: GraphCaseProfile) -> dict[str, float]:
    mechanism = _coverage(left.mechanisms, right.mechanisms)
    root_kind = _coverage(left.root_kinds, right.root_kinds)
    layer = _coverage(left.root_layers, right.root_layers)
    modality = _coverage(left.modalities, right.modalities)
    entity = _capped_overlap(left.entities, right.entities, cap=8)
    edge = _capped_overlap(left.edge_fingerprints, right.edge_fingerprints, cap=6)
    type_bonus = 0.08 if left.case_type and left.case_type == right.case_type else 0.0
    raw_similarity = min(
        1.0,
        0.30 * mechanism
        + 0.20 * root_kind
        + 0.16 * layer
        + 0.12 * modality
        + 0.14 * entity
        + 0.08 * edge
        + type_bonus,
    )
    similarity = _apply_mechanism_alignment_guard(
        raw_similarity,
        left=left,
        right=right,
        mechanism_score=mechanism,
    )
    return {
        "similarity": round(similarity, 4),
        "mechanism": round(mechanism, 4),
        "root_kind": round(root_kind, 4),
        "layer": round(layer, 4),
        "modality": round(modality, 4),
        "entity": round(entity, 4),
        "edge": round(edge, 4),
    }


def _coverage(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    if not left_set:
        return 0.0
    return len(left_set & set(right)) / len(left_set)


def _capped_overlap(left: Sequence[str], right: Sequence[str], *, cap: int) -> float:
    if not left:
        return 0.0
    return min(1.0, len(set(left) & set(right)) / float(cap))


def _overlap(left: Sequence[str], right: Sequence[str]) -> list[str]:
    return sorted(set(left) & set(right))


def _entity_tokens(tokens: set[str]) -> set[str]:
    return {
        token
        for token in tokens
        if token.startswith(ENTITY_PREFIXES)
        and not token.startswith("trace:")
        and not any(fragment in token for fragment in NOISY_ENTITY_FRAGMENTS)
    }


def _is_high_signal_root_kind(kind: str) -> bool:
    return kind.startswith("pattern_") or kind in HIGH_SIGNAL_ROOT_KINDS


def _mechanisms_from_profile_payload(
    root_payload: Sequence[dict[str, Any]],
    evidence_payload: Sequence[dict[str, Any]],
) -> set[str]:
    text = _profile_mechanism_text(root_payload, evidence_payload)
    mechanisms: set[str] = set()
    for mechanism, needles in PROFILE_MECHANISM_ALIASES.items():
        if any(needle in text for needle in needles):
            mechanisms.add(mechanism)
    return mechanisms


def _profile_mechanism_text(
    root_payload: Sequence[dict[str, Any]],
    evidence_payload: Sequence[dict[str, Any]],
) -> str:
    fragments: list[Any] = [
        {
            "kind": item.get("kind", ""),
            "label": item.get("label", ""),
            "reason": item.get("reason", ""),
        }
        for item in root_payload[:8]
    ]
    fragments.extend(item for item in evidence_payload[:32] if _is_profile_signal_evidence(item))
    return json.dumps(fragments, ensure_ascii=False).lower()


def _is_profile_signal_evidence(item: dict[str, Any]) -> bool:
    name = str(item.get("name") or "").lower()
    if name in NON_CAUSAL_PROFILE_EVIDENCE_NAMES:
        return False
    text = json.dumps(item, ensure_ascii=False).lower()
    return any(marker in text for marker in PROFILE_SIGNAL_MARKERS)


def _apply_mechanism_alignment_guard(
    raw_similarity: float,
    *,
    left: GraphCaseProfile,
    right: GraphCaseProfile,
    mechanism_score: float,
) -> float:
    if not left.mechanisms:
        return raw_similarity
    if not right.mechanisms:
        return min(0.35, raw_similarity * 0.45)
    if mechanism_score <= 0:
        return min(0.48, raw_similarity * 0.65)
    return raw_similarity


def _edge_fingerprint(*, source_kind: str, rel: str, target_kind: str) -> str:
    source = source_kind or "unknown"
    target = target_kind or "unknown"
    relation = rel or "REL"
    return f"{source}-{relation}->{target}"


def _node_kind_from_id(node_id: str) -> str:
    return node_id.split(":", 1)[0] if ":" in node_id else "unknown"


def _json_obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _categories(profile: GraphCaseProfile, matches: Sequence[GraphAnalogueMatch]) -> list[str]:
    categories: list[str] = []
    if not profile.mechanisms:
        categories.append("profile_mechanism_missing")
    if not matches:
        categories.append("no_graph_analogue")
        return categories
    top = matches[0]
    if top.similarity >= 0.8:
        categories.append("strong_graph_analogue")
    elif top.similarity >= 0.55:
        categories.append("medium_graph_analogue")
    else:
        categories.append("weak_graph_analogue")
    if top.negative_probe_count:
        categories.append("similar_case_has_negative_probe")
    if not top.matched_mechanisms:
        categories.append("top_analogue_untyped")
    if profile.mechanisms and not top.matched_mechanisms:
        categories.append("mechanism_not_reproduced")
    if profile.root_kinds and not top.matched_root_kinds:
        categories.append("root_kind_not_reproduced")
    return categories


def _feedback_ledger(leaderboard_path: Path | None, team_name: str) -> ProbeFeedbackLedger | None:
    if leaderboard_path is None or not leaderboard_path.exists():
        return None
    payload = load_json(leaderboard_path)
    if not isinstance(payload, dict):
        return None
    return ProbeFeedbackLedger.from_leaderboard(payload, team_name=team_name)


def _best_probe_accuracy(feedback: Any) -> float | None:
    if feedback is None or not feedback.records:
        return None
    return max(record.accuracy for record in feedback.records)


def _top_similarity(case: GraphAnalogueCase) -> float:
    return case.matches[0].similarity if case.matches else 0.0


def _case_suffix(case_id: str) -> str:
    return case_id.rsplit("-", 1)[-1][-4:]


def _top_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    return ", ".join(f"{name}={count}" for name, count in Counter(counts).most_common(limit))


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
