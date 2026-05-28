# Synthetic RDS Suite — Agent Notes

## Baseline Contract

`_baseline/<scenario_id>.json` holds the committed `canonical_report_payload` for each
scenario, produced by the pure scoring path with an **empty final state** (no LLM calls).
This is a contract file: every agent that changes the canonical payload schema MUST update
the corresponding `_baseline/*.json` files in the **same PR**.

### Regenerating baselines

```bash
uv run python -c "
from pathlib import Path
import json
from dataclasses import asdict
from datetime import UTC, datetime
from tests.synthetic.rds_postgres.run_suite import (
    _resolved_golden_trajectory, _trajectory_policy_for_fixture,
    _apply_trajectory_policy_to_score, score_result,
)
from tests.synthetic.rds_postgres.observations import (
    build_observation, compute_trajectory_metrics, evaluate_trajectory_policy,
)
from tests.synthetic.rds_postgres.scenario_loader import load_all_scenarios, SUITE_DIR

baseline_dir = Path('tests/synthetic/rds_postgres/_baseline')
baseline_dir.mkdir(parents=True, exist_ok=True)
for fixture in load_all_scenarios(SUITE_DIR):
    final_state = {
        'root_cause': '', 'root_cause_category': 'unknown',
        'validated_claims': [], 'non_validated_claims': [], 'causal_chain': [],
        'evidence': {}, 'executed_hypotheses': [], 'investigation_loop_count': 0, 'report': '',
    }
    score = score_result(fixture, final_state)
    golden_trajectory, max_loops, golden_cfg = _resolved_golden_trajectory(fixture)
    trajectory_metrics = compute_trajectory_metrics(
        executed_hypotheses=[], golden=golden_trajectory, loops_used=0, max_loops=max_loops,
    )
    trajectory_policy = (
        evaluate_trajectory_policy(
            metrics=trajectory_metrics, golden_actions=golden_trajectory,
            policy=_trajectory_policy_for_fixture(max_loops=max_loops, golden_cfg=golden_cfg),
        )
        if golden_cfg is not None else None
    )
    score = _apply_trajectory_policy_to_score(score, trajectory_policy)
    obs = build_observation(
        scenario_id=fixture.scenario_id, suite='axis1', backend='FixtureGrafanaBackend',
        score=asdict(score), reasoning=None, trajectory=trajectory_metrics,
        evaluated_golden_actions=golden_trajectory, trajectory_policy=trajectory_policy,
        final_state=final_state, available_evidence_sources=list(fixture.metadata.available_evidence),
        required_evidence_sources=list(fixture.answer_key.required_evidence_sources),
        started_at=datetime.now(UTC), wall_time_s=0.0,
    )
    (baseline_dir / f'{fixture.scenario_id}.json').write_text(
        json.dumps(obs.canonical_report_payload, indent=2, sort_keys=True), encoding='utf-8'
    )
    print(f'  wrote {fixture.scenario_id}')
"
```

### Checking baselines

```bash
uv run python -m tests.synthetic.rds_postgres.run_suite \
  --mock-grafana \
  --scenario 001-replication-lag \
  --baseline-check tests/synthetic/rds_postgres/_baseline
```

## Re-export shims

`observations.py` re-exports `TrajectoryPolicy`, `TrajectoryPolicyResult`, and
`evaluate_trajectory_policy` from `trajectory_policy.py` (introduced in Phase 2).
These shims are temporary; remove them once all import sites are updated.

## Module layout (after all phases)

| Module | Purpose |
|---|---|
| `scenario_loader.py` | Load fixture YAML/JSON into typed dataclasses |
| `evidence_sources.py` | Semantic evidence-source IDs and predicates (Phase 1) |
| `trajectory_policy.py` | Pure policy evaluator, no rich/console imports (Phase 2) |
| `scoring.py` | Pure scoring: `score_result`, keyword matching, gates (Phase 3) |
| `observations.py` | Trajectory metrics, observation builder, artifact writer, console rendering |
| `reporting.py` | Cross-axis gap report (Phase 3) |
| `run_suite.py` | Thin orchestration: arg parsing, loop, seam to `app.pipeline.runners` |
