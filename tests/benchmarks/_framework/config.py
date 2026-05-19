from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_WORKERS = min(8, os.cpu_count() or 1)
DEFAULT_MODES = ("opensre+llm",)
DEFAULT_REPORT_FORMATS = ("json", "markdown")


@dataclass(frozen=True)
class BenchmarkConfig:
    benchmark: str
    modes: tuple[str, ...] = DEFAULT_MODES
    llms: tuple[str, ...] = ()
    runs_per_case: int = 1
    workers: int = DEFAULT_WORKERS
    cost_budget_usd: float | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    output_dir: str = ".bench-results/latest"
    report_formats: tuple[str, ...] = DEFAULT_REPORT_FORMATS
    strict_parity: bool = False


def _as_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(item.strip() for item in value if item.strip())


def _positive_int(raw: Any, *, field_name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return value


def benchmark_config_from_dict(payload: dict[str, Any]) -> BenchmarkConfig:
    benchmark = str(payload.get("benchmark") or "").strip()
    if not benchmark:
        raise ValueError("benchmark is required")

    filters = payload.get("filters") or {}
    if not isinstance(filters, dict):
        raise ValueError("filters must be an object")

    modes = _as_tuple(payload.get("modes", list(DEFAULT_MODES)), field_name="modes")
    if not modes:
        modes = DEFAULT_MODES
    unsupported_modes = sorted(set(modes) - {"opensre+llm"})
    if unsupported_modes:
        raise ValueError(
            f"unsupported modes: {', '.join(unsupported_modes)}. "
            "The framework compares against published LLM-alone paper baselines "
            "in reports; it does not execute an LLM-alone runner."
        )

    formats = _as_tuple(
        payload.get("report_formats", list(DEFAULT_REPORT_FORMATS)),
        field_name="report_formats",
    )
    if not formats:
        formats = DEFAULT_REPORT_FORMATS
    unsupported_formats = sorted(set(formats) - {"json", "markdown", "html"})
    if unsupported_formats:
        raise ValueError(f"unsupported report formats: {', '.join(unsupported_formats)}")

    cost_budget_raw = payload.get("cost_budget_usd")
    cost_budget = float(cost_budget_raw) if cost_budget_raw is not None else None
    if cost_budget is not None and cost_budget <= 0:
        raise ValueError("cost_budget_usd must be > 0")

    return BenchmarkConfig(
        benchmark=benchmark,
        modes=modes,
        llms=_as_tuple(payload.get("llms", []), field_name="llms"),
        runs_per_case=_positive_int(payload.get("runs_per_case", 1), field_name="runs_per_case"),
        workers=_positive_int(payload.get("workers", DEFAULT_WORKERS), field_name="workers"),
        cost_budget_usd=cost_budget,
        filters=dict(filters),
        output_dir=str(payload.get("output_dir") or ".bench-results/latest"),
        report_formats=formats,
        strict_parity=bool(payload.get("strict_parity", False)),
    )


def load_benchmark_config(path: str | Path) -> BenchmarkConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: expected a YAML object")
    return benchmark_config_from_dict(raw)
