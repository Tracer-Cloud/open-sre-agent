"""End-to-end smoke test for the benchmark framework + CloudOpsBench adapter.

Two stages, exposed via flags:

1. ``--adapter-only`` (default): load 1 case, build alert + integrations,
   exercise score_case with a fake RunResult. No LLM, no cost, no opensre
   pipeline. Verifies the adapter wiring end-to-end.

2. ``--run-investigation``: actually invoke ``run_investigation`` from
   ``core.orchestration.entrypoints`` against the loaded case. Requires an LLM to
   be configured. Costs a few cents per case. Verifies the full chain.

Usage::

    uv run python -m tests.benchmarks._framework.smoke                       # adapter-only
    uv run python -m tests.benchmarks._framework.smoke --run-investigation   # full chain
    uv run python -m tests.benchmarks._framework.smoke --limit 3 --seed 42   # 3 cases
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from typing import Any, cast

from tests.benchmarks._framework.adapters import (
    BenchmarkAdapter,
    BenchmarkCase,
    CaseFilters,
    RunContext,
    RunResult,
    build_adapter,
)


def _fake_run_result(case: BenchmarkCase) -> RunResult:
    """A plausible RunResult for adapter-only smoke testing.

    The fake claims a wrong root cause so we can confirm the scorer
    actually penalizes it (rather than silently returning all 1.0s).
    """
    now = datetime.now(UTC).isoformat()
    return RunResult(
        case_id=case.case_id,
        mode="opensre+llm",
        llm="fake-llm",
        model_version="fake-llm-0.0",
        opensre_sha="HEAD-uncommitted",
        started_at=now,
        ended_at=now,
        ok=True,
        error=None,
        final_diagnosis={
            "stage": "Runtime",
            "component": "wrong-pod",
            "root_cause": "deliberately-wrong-cause",
        },
        evidence_entries=[],
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=0,
    )


def _real_run_result(case: BenchmarkCase, adapter: BenchmarkAdapter) -> RunResult:
    """Invoke opensre's run_investigation for real. Requires LLM credentials."""
    # Late import — only needed in this branch, keeps adapter-only path
    # importable without the full opensre dep tree.
    from core.orchestration.entrypoints import run_investigation

    alert = adapter.build_alert(case)
    integrations = adapter.build_opensre_integrations(case)
    started = datetime.now(UTC)
    t0 = time.monotonic()

    final_state = run_investigation(
        alert.raw,
        resolved_integrations=integrations,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    ended = datetime.now(UTC)

    final_state_dict = dict(final_state)
    return RunResult(
        case_id=case.case_id,
        mode="opensre+llm",
        llm="(opensre-default)",  # llm_dispatch not yet implemented; uses opensre's config
        model_version="(unpinned)",  # llm_dispatch not yet implemented
        opensre_sha="HEAD-uncommitted",
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        ok=True,
        error=None,
        final_diagnosis={
            "stage": final_state_dict.get("root_cause_category") or "",
            "component": "",  # opensre doesn't return this field directly
            "root_cause": final_state_dict.get("root_cause") or "",
            "report": final_state_dict.get("report") or "",
        },
        evidence_entries=list(cast(list[Any], final_state_dict.get("evidence_entries") or [])),
        tokens_in=0,  # cost.py will fill this in later
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=latency_ms,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1, help="Number of cases to load.")
    parser.add_argument("--seed", type=int, default=42, help="Seed for case selection.")
    parser.add_argument(
        "--run-investigation",
        action="store_true",
        help="Actually invoke opensre's run_investigation (requires LLM config + costs $).",
    )
    parser.add_argument(
        "--adapter-only",
        action="store_true",
        help="Skip run_investigation; use a fake RunResult to test the adapter only.",
    )
    parser.add_argument(
        "--adapter",
        default="cloudopsbench",
        help=(
            "Name of the registered benchmark adapter to smoke-test. Default "
            "is ``cloudopsbench`` (the only adapter shipped today)."
        ),
    )
    args = parser.parse_args(argv)

    use_real_runner = args.run_investigation and not args.adapter_only

    # Resolved through the registry so this smoke does not hold a direct
    # import to any specific adapter. The adapter name comes from a CLI
    # flag; default is CloudOpsBench because it's the only registered
    # adapter today, and a non-CloudOpsBench user can pass
    # ``--adapter <name>`` without source changes here.
    adapter_name = args.adapter
    print(f"==> {adapter_name} adapter end-to-end smoke")
    print(
        f"    limit={args.limit}  seed={args.seed}  mode={'real' if use_real_runner else 'adapter-only'}"
    )
    print()

    adapter = build_adapter(adapter_name)

    print("==> Loading cases")
    cases = list(adapter.load_cases(CaseFilters(limit=args.limit, seed=args.seed)))
    if not cases:
        print(f"  ✗ No cases loaded from adapter {adapter_name!r}.")
        print(f"    Check that the {adapter_name} corpus is available on disk")
        print("    (for cloudopsbench: ``make download-cloudopsbench-hf``).")
        return 1
    print(f"  ✓ loaded {len(cases)} case(s)")
    for c in cases:
        gt: dict[str, Any] = c.metadata.get("ground_truth", {})
        print(
            f"    {c.case_id}\n"
            f"      system={c.metadata.get('system')}  "
            f"fault_category={c.metadata.get('fault_category')}\n"
            f"      true root_cause={gt.get('root_cause')!r}"
        )
    print()

    for case in cases:
        print(f"==> Case {case.case_id}")

        # 1. build_alert
        alert = adapter.build_alert(case)
        print(f"  ✓ build_alert: raw_keys={sorted(alert.raw.keys())[:6]}...")

        # 2. build_opensre_integrations
        integrations = adapter.build_opensre_integrations(case)
        backend_obj = integrations.get("eks", {}).get("_backend")
        backend_type = type(backend_obj).__name__ if backend_obj is not None else "MISSING"
        print(
            f"  ✓ build_opensre_integrations: integration keys={sorted(integrations.keys())}; "
            f"eks._backend type={backend_type}"
        )

        # 3. Run (real or fake)
        if use_real_runner:
            print("  ▶ running opensre investigation (this takes time + costs $)...")
            try:
                run = _real_run_result(case, adapter)
                print(f"  ✓ run completed in {run.latency_ms}ms")
                print(f"    final_diagnosis.root_cause={run.final_diagnosis.get('root_cause')!r}")
            except Exception as exc:
                print(f"  ✗ run_investigation failed: {exc}")
                print("    Check: ~/.opensre/integrations.json + LLM API key")
                return 2
        else:
            run = _fake_run_result(case)
            print("  ✓ fake RunResult built (claims wrong root cause to verify scoring)")

        # 4. score_case — pass the same integrations dict via RunContext
        score = adapter.score_case(case, run, RunContext(integrations=integrations))
        if score.failure_reason:
            print(f"  ✗ scoring failed: {score.failure_reason}")
            return 3

        print(f"  ✓ score_case: {len(score.metrics)} metrics emitted")
        for metric_name in sorted(score.metrics.keys()):
            value = score.metrics[metric_name]
            print(f"    {metric_name:>14} = {value:.3f}")
        print()

    print("==> ✓ End-to-end smoke passed")
    print()
    print("Per-case sanity check on the metrics above:")
    print("  * adapter-only mode injects a wrong root cause →")
    print("    expect a1=0, a3=0, partial_a1=0, partial_a3=0, tcr=1 (output well-formed)")
    print("  * process metrics (exact, in_order, any_order, rel, cov) measure tool-use")
    print("    coverage — fake run has 0 tool calls so these should be near 0")
    print("  * iac, rar, ztdr should be 0 (no actions = no invalid/redundant/zero-tool)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
