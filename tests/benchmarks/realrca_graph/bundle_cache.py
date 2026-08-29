from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.bundle import build_evidence_bundle
from tests.benchmarks.realrca_graph.io import REALRCA_GRAPH, load_json
from tests.benchmarks.realrca_graph.models import EvidenceBundle, EvidenceItem, RootHypothesis

DEFAULT_BUNDLE_CACHE_DIR = REALRCA_GRAPH / "bundle-cache"
BUNDLE_CACHE_VERSION = "v1-hsf-sql-mechanisms"
_SOURCE_FILES = (
    "bundle.py",
    "features.py",
    "root_patterns.py",
    "summaries.py",
    "summary_cache.py",
    "topology.py",
)


def build_evidence_bundle_cached(
    graph_path: Path,
    *,
    evidence_limit: int = 32,
    hypothesis_limit: int = 10,
    support_limit: int = 4,
    cache_dir: Path = DEFAULT_BUNDLE_CACHE_DIR,
) -> EvidenceBundle:
    """Build a bundle from a graph_context path, reusing a versioned disk cache."""

    cache_path = _cache_path(
        graph_path,
        evidence_limit=evidence_limit,
        hypothesis_limit=hypothesis_limit,
        support_limit=support_limit,
        cache_dir=cache_dir,
    )
    cached = _read_cached_bundle(cache_path)
    if cached is not None:
        return cached
    bundle = build_evidence_bundle(
        load_json(graph_path),
        evidence_limit=evidence_limit,
        hypothesis_limit=hypothesis_limit,
        support_limit=support_limit,
    )
    _write_cached_bundle(cache_path, bundle)
    return bundle


def _cache_path(
    graph_path: Path,
    *,
    evidence_limit: int,
    hypothesis_limit: int,
    support_limit: int,
    cache_dir: Path,
) -> Path:
    try:
        stat = graph_path.stat()
        resolved_path = graph_path.resolve()
    except OSError:
        resolved_path = graph_path
        stat = None
    payload = {
        "version": BUNDLE_CACHE_VERSION,
        "graph_path": str(resolved_path),
        "graph_size": stat.st_size if stat is not None else None,
        "graph_mtime_ns": stat.st_mtime_ns if stat is not None else None,
        "evidence_limit": evidence_limit,
        "hypothesis_limit": hypothesis_limit,
        "support_limit": support_limit,
        "source_signature": _source_signature(),
    }
    key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return cache_dir / key[:2] / f"{key}.json"


@lru_cache(maxsize=1)
def _source_signature() -> tuple[tuple[str, int, int], ...]:
    base = Path(__file__).resolve().parent
    signature: list[tuple[str, int, int]] = []
    for filename in _SOURCE_FILES:
        path = base / filename
        try:
            stat = path.stat()
        except OSError:
            signature.append((filename, -1, -1))
            continue
        signature.append((filename, stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


def _read_cached_bundle(cache_path: Path) -> EvidenceBundle | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_bundle = payload.get("bundle")
    if not isinstance(raw_bundle, dict):
        return None
    try:
        return _bundle_from_dict(raw_bundle)
    except (TypeError, ValueError, KeyError):
        return None


def _write_cached_bundle(cache_path: Path, bundle: EvidenceBundle) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps({"bundle": bundle.to_dict()}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(cache_path)
    except OSError:
        return


def _bundle_from_dict(payload: dict[str, Any]) -> EvidenceBundle:
    return EvidenceBundle(
        case_id=str(payload.get("case_id") or ""),
        split=str(payload.get("split") or ""),
        case_type=str(payload.get("case_type") or ""),
        data_ref=str(payload.get("data_ref") or ""),
        ontology=[str(item) for item in payload.get("ontology") or []],
        retrieval_summary=str(payload.get("retrieval_summary") or ""),
        evidence=[
            _evidence_from_dict(item)
            for item in payload.get("evidence") or []
            if isinstance(item, dict)
        ],
        hypotheses=[
            _hypothesis_from_dict(item)
            for item in payload.get("hypotheses") or []
            if isinstance(item, dict)
        ],
    )


def _evidence_from_dict(payload: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        modality=str(payload.get("modality") or ""),
        summary=str(payload.get("summary") or ""),
        command=str(payload.get("command") or ""),
        raw_ref=str(payload.get("raw_ref") or ""),
        score=_float(payload.get("score")),
    )


def _hypothesis_from_dict(payload: dict[str, Any]) -> RootHypothesis:
    raw_entities = payload.get("entities")
    entities = (
        {
            str(key): [str(item) for item in value]
            for key, value in raw_entities.items()
            if isinstance(value, list)
        }
        if isinstance(raw_entities, dict)
        else {}
    )
    return RootHypothesis(
        id=str(payload.get("id") or ""),
        kind=str(payload.get("kind") or ""),
        label=str(payload.get("label") or ""),
        root_layer=str(payload.get("root_layer") or ""),
        score=_float(payload.get("score")),
        reason=str(payload.get("reason") or ""),
        entities=entities,
        modalities=[str(item) for item in payload.get("modalities") or []],
        support=[
            _evidence_from_dict(item)
            for item in payload.get("support") or []
            if isinstance(item, dict)
        ],
        contradictions=[str(item) for item in payload.get("contradictions") or []],
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
