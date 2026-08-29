from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tests.benchmarks.realrca_graph.io import REALRCA_GRAPH
from tests.benchmarks.realrca_graph.summaries import compact_evidence_summary

SUMMARY_CACHE_VERSION = "v4-fallback-when-raw-empty"
DEFAULT_SUMMARY_CACHE_DIR = REALRCA_GRAPH / "summary-cache"
DEFAULT_MAX_RAW_JSON_BYTES = 8_000_000


def compact_evidence_summary_cached(
    name: str,
    command: str,
    raw_ref: str,
    fallback: Any,
    *,
    cache_dir: Path = DEFAULT_SUMMARY_CACHE_DIR,
    max_raw_json_bytes: int = DEFAULT_MAX_RAW_JSON_BYTES,
) -> str:
    """Return a compact evidence summary, caching raw-file parsing by content metadata."""

    raw_path = Path(raw_ref).expanduser() if raw_ref else None
    source, fallback_key = _summary_source(
        raw_path, fallback, max_raw_json_bytes=max_raw_json_bytes
    )
    cache_path = _cache_path(
        raw_path,
        name=name,
        command=command,
        cache_dir=cache_dir,
        max_raw_json_bytes=max_raw_json_bytes,
        fallback_key=fallback_key,
    )
    if cache_path is not None:
        cached = _read_cached_summary(cache_path)
        if cached is not None:
            return cached

    summary = compact_evidence_summary(name, command, source)
    if cache_path is not None:
        _write_cached_summary(cache_path, summary)
    return summary


def _summary_source(
    raw_path: Path | None, fallback: Any, *, max_raw_json_bytes: int
) -> tuple[Any, str]:
    if raw_path is None:
        return fallback, _fallback_key(fallback)
    try:
        if not raw_path.is_file() or raw_path.stat().st_size > max_raw_json_bytes:
            return fallback, _fallback_key(fallback)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if _is_empty_source(payload) and _has_nonempty_fallback(fallback):
            return fallback, _fallback_key(fallback)
        return payload, ""
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return fallback, _fallback_key(fallback)


def _cache_path(
    raw_path: Path | None,
    *,
    name: str,
    command: str,
    cache_dir: Path,
    max_raw_json_bytes: int,
    fallback_key: str,
) -> Path | None:
    if raw_path is None:
        return None
    try:
        stat = raw_path.stat()
    except OSError:
        return None
    payload = {
        "version": SUMMARY_CACHE_VERSION,
        "path": str(raw_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "name": name,
        "command": command,
        "max_raw_json_bytes": max_raw_json_bytes,
        "fallback_key": fallback_key,
    }
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return cache_dir / key[:2] / f"{key}.json"


def _read_cached_summary(cache_path: Path) -> str | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    return summary if isinstance(summary, str) else None


def _write_cached_summary(cache_path: Path, summary: str) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps({"summary": summary}, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError:
        return


def _has_nonempty_fallback(fallback: Any) -> bool:
    return str(fallback or "").strip() not in {"", "[]", "{}"}


def _is_empty_source(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in {"", "[]", "{}"}
    if isinstance(value, list):
        return len(value) == 0
    if isinstance(value, dict):
        if "result" in value and len(value) <= 2:
            return _is_empty_source(value.get("result"))
        counts = [value.get(key) for key in ("count", "total", "totalCount", "size")]
        numeric_counts = [item for item in counts if isinstance(item, int)]
        if numeric_counts and max(numeric_counts) == 0:
            return True
        return all(_is_empty_source(item) for item in value.values())
    return False


def _fallback_key(fallback: Any) -> str:
    if not _has_nonempty_fallback(fallback):
        return ""
    try:
        body = json.dumps(fallback, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        body = str(fallback)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
