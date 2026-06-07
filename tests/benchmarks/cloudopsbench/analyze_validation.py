"""Analyze a Fix-A + vocab-fix validation run (read-only over cases/*.json).

Compares the ``opensre+llm`` and ``llm_alone`` arms per shape stratum and
reports the metrics that the 2026-06-07 fixes target:

  - per-stratum a1 / object_a1 / false-healthy rate per arm
  - the paired ``opensre+llm − llm_alone`` a1 contrast (scenario-clustered
    bootstrap CI) — the headline number Fix A should move positive on
    seen-shape
  - translation-loss proxy: among opensre+llm failures, how often opensre's
    report NAMED the correct fault_object but the predictor's top-3 dropped it
    (this is the 15.2%-vs-5.7% leak Fix A closes)

Usage:
    uv run python -m tests.benchmarks.cloudopsbench.analyze_validation \
        .bench-results/cloudopsbench_fixa_validation_openai/<run-id>

Pass a run directory (the one containing ``cases/``). Exploratory only — this
is a dev-pilot analyzer, not a publication report generator.
"""

from __future__ import annotations

import glob
import json
import random
import sys
from pathlib import Path

_ARMS = ("opensre+llm", "llm_alone")

# Minimum service-name length for the seen-shape translation-loss substring
# proxy. All seen-shape ground-truth objects are ``app/<service>`` with
# specific multi-token names (e.g. ``ts-voucher-service``), so a substring
# match in the report is a reliable "the investigation named this service"
# signal. We do NOT gate on a hard-coded service list — the corpus has more
# services (esp. trainticket) than any short allowlist, and an incomplete list
# silently undercounts the leak.
_MIN_SERVICE_NAME_LEN = 4


def _norm(s: object) -> str:
    return str(s or "").strip().lower()


def _load(run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(glob.glob(str(run_dir / "cases" / "*.json"))):
        try:
            rows.append(json.loads(Path(fp).read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return rows


def _gt(case: dict) -> tuple[str, str, str]:
    g = case.get("metadata", {}).get("ground_truth", {})
    return _norm(g.get("fault_object")), _norm(g.get("root_cause")), _norm(g.get("fault_taxonomy"))


def _top(run: dict) -> list[dict]:
    return (run.get("final_diagnosis") or {}).get("top_3_predictions") or []


def _is_a1(pred: dict, gt: tuple[str, str, str]) -> bool:
    go, gr, gtax = gt
    return (
        _norm(pred.get("fault_object")) == go
        and _norm(pred.get("root_cause")) == gr
        and _norm(pred.get("fault_taxonomy")) == gtax
    )


def _bootstrap_ci(deltas: list[float], iters: int = 2000) -> tuple[float, float, float]:
    if not deltas:
        return float("nan"), float("nan"), float("nan")
    pt = sum(deltas) / len(deltas)
    random.seed(42)
    boots = []
    for _ in range(iters):
        samp = [deltas[random.randrange(len(deltas))] for _ in deltas]
        boots.append(sum(samp) / len(samp))
    boots.sort()
    return pt, boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]


def analyze(run_dir: Path) -> int:
    rows = _load(run_dir)
    if not rows:
        print(f"No case files found under {run_dir}/cases/")
        return 1

    print(f"Validation analysis: {run_dir.name}  ({len(rows)} cells)\n")

    # Per-stratum per-arm summary.
    for seen in (True, False):
        label = "SEEN-shape" if seen else "UNSEEN-shape"
        print(f"=== {label} ===")
        print(f"{'arm':<14}{'n':>5}{'a1':>8}{'object_a1':>11}{'healthy%':>10}")
        for arm in _ARMS:
            cells = [
                r for r in rows if r["run"]["mode"] == arm and r["case"].get("seen_shape") is seen
            ]
            if not cells:
                continue
            a1 = obj = healthy = 0
            for r in cells:
                gt = _gt(r["case"])
                preds = _top(r["run"])
                p1 = preds[0] if preds else {}
                if preds and _is_a1(p1, gt):
                    a1 += 1
                if preds and _norm(p1.get("fault_object")) == gt[0]:
                    obj += 1
                if _norm((r["run"].get("final_diagnosis") or {}).get("stage")) == "healthy":
                    healthy += 1
            n = len(cells)
            print(f"{arm:<14}{n:>5}{a1 / n:>8.3f}{obj / n:>11.3f}{100 * healthy / n:>9.1f}%")
        print()

    # Paired contrast (opensre+llm − llm_alone) per stratum, scenario-clustered.
    print("=== paired a1 contrast: (opensre+llm) − (llm_alone) ===")
    for seen in (True, False, None):
        label = {True: "seen", False: "unseen", None: "all"}[seen]

        def scen_a1(arm: str, seen: bool | None = seen) -> dict[str, float]:
            by: dict[str, list[int]] = {}
            for r in rows:
                if r["run"]["mode"] != arm:
                    continue
                if seen is not None and r["case"].get("seen_shape") is not seen:
                    continue
                preds = _top(r["run"])
                hit = 1 if (preds and _is_a1(preds[0], _gt(r["case"]))) else 0
                by.setdefault(r["case"]["case_id"], []).append(hit)
            return {k: sum(v) / len(v) for k, v in by.items()}

        a = scen_a1("opensre+llm")
        b = scen_a1("llm_alone")
        shared = sorted(set(a) & set(b))
        deltas = [a[k] - b[k] for k in shared]
        pt, lo, hi = _bootstrap_ci(deltas)
        verdict = (
            "ns (incl 0)" if (lo <= 0 <= hi) else ("opensre+ SIG" if pt > 0 else "control+ SIG")
        )
        print(
            f"  {label:<7} d={pt:+.4f}  95%CI[{lo:+.4f},{hi:+.4f}]  n_scen={len(shared):>3}  {verdict}"
        )
    print()

    # Translation-loss proxy (seen-shape, the Fix-A target).
    print("=== translation-loss proxy (seen-shape failures) ===")
    print("    report NAMED correct fault_object but predictor dropped it from top-3")
    for arm in _ARMS:
        fails = dropped = 0
        for r in rows:
            if r["run"]["mode"] != arm or not r["case"].get("seen_shape"):
                continue
            gt = _gt(r["case"])
            preds = _top(r["run"])
            p1 = preds[0] if preds else {}
            if preds and _is_a1(p1, gt):
                continue
            fails += 1
            gt_name = gt[0].split("/")[-1]
            report = _norm((r["run"].get("final_diagnosis") or {}).get("report"))
            named = len(gt_name) >= _MIN_SERVICE_NAME_LEN and gt_name in report
            in_top3 = any(_norm(p.get("fault_object")) == gt[0] for p in preds)
            if named and not in_top3:
                dropped += 1
        if fails:
            print(f"  {arm:<14} {dropped}/{fails} = {100 * dropped / fails:.1f}% of failures")
    print()

    # B2 false-healthy guard activations (Path B, 2026-06-07).
    # The guard rewrites a false-healthy investigation to root_cause_category=unknown
    # with a fixed signature string. Detect fired cells by that signature so the
    # analyzer can split fired vs non-fired a1 per arm — the headline B2 impact.
    print("=== B2 false-healthy guard activations ===")
    print("    cells where the guard downgraded a false-healthy conclusion")
    any_fired = False
    for arm in _ARMS:
        cells = [r for r in rows if r["run"]["mode"] == arm]
        if not cells:
            continue
        fired = [r for r in cells if _b2_fired(r)]
        non_fired = [r for r in cells if not _b2_fired(r)]
        if not fired:
            print(f"  {arm:<14} 0 / {len(cells)} cells fired")
            continue
        any_fired = True
        fired_a1 = sum(1 for r in fired if _cell_a1(r))
        non_fired_a1 = sum(1 for r in non_fired if _cell_a1(r))
        fire_rate = 100 * len(fired) / len(cells)
        non_fired_a1_rate = non_fired_a1 / len(non_fired) if non_fired else 0.0
        print(
            f"  {arm:<14} {len(fired):3d} / {len(cells):3d} = {fire_rate:5.1f}% fired  "
            f"|  fired a1={fired_a1 / len(fired):.3f}  non-fired a1={non_fired_a1_rate:.3f}"
        )
    if not any_fired:
        print(
            "  (no activations detected — either the guard wasn't enabled, "
            "evidence_entries weren't persisted, or no cell matched both conditions)"
        )
    return 0


# Detect a B2 guard activation by the downgrade signature. Keep this marker
# phrase in lockstep with ``false_healthy_guard._DOWNGRADE_ROOT_CAUSE``.
def _b2_fired(row: dict) -> bool:
    diag = row["run"].get("final_diagnosis") or {}
    rc = _norm(diag.get("root_cause"))
    return "tool observations show unhealthy" in rc and "marked unresolved" in rc


def _cell_a1(row: dict) -> int:
    preds = _top(row["run"])
    return 1 if preds and _is_a1(preds[0], _gt(row["case"])) else 0


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: analyze_validation.py <run-dir-containing-cases/>")
        return 2
    return analyze(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
