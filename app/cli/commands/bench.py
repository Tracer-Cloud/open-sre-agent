"""Benchmark CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from app.cli.support.errors import OpenSREError


@click.group(name="bench")
def bench_command() -> None:
    """Run and report OpenSRE benchmark suites."""


@bench_command.command(name="list")
def list_benchmarks() -> None:
    """Show available benchmark adapters."""
    from tests.benchmarks._framework.registry import available_adapters

    for name in sorted(available_adapters()):
        click.echo(name)


@bench_command.command(name="validate")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
def validate_config(config: str) -> None:
    """Validate a benchmark YAML config without executing cases."""
    from tests.benchmarks._framework.config import load_benchmark_config
    from tests.benchmarks._framework.registry import create_adapter

    try:
        loaded = load_benchmark_config(config)
        create_adapter(loaded.benchmark)
    except Exception as exc:
        raise OpenSREError(str(exc)) from exc
    click.echo(f"OK: {config}")


@bench_command.command(name="run")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--benchmark", default="", help="Benchmark adapter name for ad-hoc runs.")
@click.option("--llm", "llms", multiple=True, help="LLM alias or provider:model. Repeatable.")
@click.option("--workers", default=0, type=int, help="Override worker count.")
@click.option("--limit", default=0, type=int, help="Limit cases for ad-hoc runs.")
@click.option("--output-dir", default="", help="Override result output directory.")
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable summary.")
def run_benchmark(
    config_path: str | None,
    benchmark: str,
    llms: tuple[str, ...],
    workers: int,
    limit: int,
    output_dir: str,
    output_json: bool,
) -> None:
    """Run a benchmark from YAML or ad-hoc options."""
    from tests.benchmarks._framework.config import BenchmarkConfig, load_benchmark_config
    from tests.benchmarks._framework.registry import create_adapter
    from tests.benchmarks._framework.runner import run_benchmark as run_framework_benchmark

    try:
        if config_path:
            loaded = load_benchmark_config(config_path)
            filters = dict(loaded.filters)
            if limit:
                filters["limit"] = limit
            config = BenchmarkConfig(
                benchmark=loaded.benchmark,
                modes=loaded.modes,
                llms=llms or loaded.llms,
                runs_per_case=loaded.runs_per_case,
                workers=workers or loaded.workers,
                cost_budget_usd=loaded.cost_budget_usd,
                filters=filters,
                output_dir=output_dir or loaded.output_dir,
                report_formats=loaded.report_formats,
                strict_parity=loaded.strict_parity,
            )
        else:
            if not benchmark:
                raise OpenSREError(
                    "Missing --config or --benchmark.",
                    suggestion="Run 'opensre bench run --config tests/benchmarks/configs/claude-vs-paper.yml'.",
                )
            filters: dict[str, Any] = {}
            if limit:
                filters["limit"] = limit
            config = BenchmarkConfig(
                benchmark=benchmark,
                llms=llms,
                workers=workers or 8,
                filters=filters,
                output_dir=output_dir or ".bench-results/latest",
                report_formats=("json", "markdown"),
            )
        result = run_framework_benchmark(create_adapter(config.benchmark), config)
    except OpenSREError:
        raise
    except Exception as exc:
        raise OpenSREError(str(exc)) from exc

    if output_json:
        click.echo(json.dumps(result.payload, indent=2))
    else:
        click.echo(f"Wrote benchmark reports to {result.output_dir}")


@bench_command.command(name="report")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--format", "formats", multiple=True, default=("markdown",))
def report_benchmark(run_dir: str, formats: tuple[str, ...]) -> None:
    """Regenerate benchmark reports from a run directory summary."""
    from tests.benchmarks._framework.reporting import write_reports

    summary_path = Path(run_dir) / "summary.json"
    if not summary_path.is_file():
        raise OpenSREError(f"No summary.json found in {run_dir}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    write_reports(payload, Path(run_dir), tuple(formats))
    click.echo(f"Wrote {', '.join(formats)} report(s) to {run_dir}")


@bench_command.command(name="compare")
@click.argument("left", type=click.Path(exists=True, file_okay=False))
@click.argument("right", type=click.Path(exists=True, file_okay=False))
def compare_benchmarks(left: str, right: str) -> None:
    """Compare two benchmark summary directories."""
    left_payload = _read_summary(left)
    right_payload = _read_summary(right)
    click.echo(json.dumps(_compare_payloads(left_payload, right_payload), indent=2))


def _read_summary(run_dir: str) -> dict[str, Any]:
    path = Path(run_dir) / "summary.json"
    if not path.is_file():
        raise OpenSREError(f"No summary.json found in {run_dir}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise OpenSREError(f"{path}: expected JSON object")
    return payload


def _compare_payloads(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {
        "left": {
            "benchmark": left.get("benchmark"),
            "runs": len(left.get("results", [])),
            "cost_usd": left.get("cost_usd", 0.0),
        },
        "right": {
            "benchmark": right.get("benchmark"),
            "runs": len(right.get("results", [])),
            "cost_usd": right.get("cost_usd", 0.0),
        },
        "delta": {
            "runs": len(right.get("results", [])) - len(left.get("results", [])),
            "cost_usd": float(right.get("cost_usd", 0.0)) - float(left.get("cost_usd", 0.0)),
        },
    }
