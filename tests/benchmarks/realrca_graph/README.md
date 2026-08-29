# RealRCA Graph Harness

This package is an offline iteration harness for RealRCA/OpenSRE benchmark
answers. It builds graph-shaped evidence from available benchmark artifacts,
scores candidate diagnoses before submission, and records why a candidate is
safe, risky, or still blocked.

The harness is intentionally conservative: it is designed to avoid submitting a
candidate unless local evidence, verifier checks, historical probe feedback, and
score-boundary analysis agree that the candidate can improve the current best
result.

## Architecture

The code is split into small stages that can be run independently from
`tests.benchmarks.realrca_graph.cli`.

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| Data access | `io.py`, `models.py`, `graph_store.py` | Load benchmark rows, graph contexts, candidate outputs, and local graph indexes. |
| Evidence extraction | `bundle.py`, `raw_inventory.py`, `summaries.py`, `app_logs.py`, `sql_logs.py`, `access_logs.py`, `runtime_metrics.py`, `rds_sql.py`, `custom_monitor.py`, `topology.py` | Convert logs, metrics, traces, SQL evidence, alarms, and topology into normalized evidence bundles. |
| Knowledge graph | `ontology_graph.py`, `causal_paths.py`, `graph_analogues.py`, `case_analogues.py`, `trajectory_evidence.py`, `trajectory_mining.py` | Represent system entities, failure mechanisms, causal paths, historical analogues, and agent trajectories. |
| Candidate generation | `generation.py`, `synthesis.py`, `answer_seeds.py`, `answer_anchors.py`, `trace_repair.py` | Produce or repair answer candidates and trace IDs from graph evidence. |
| Verification | `verifier.py`, `llm_verifier.py`, `alignment.py`, `answer_contract.py`, `mechanism_terms.py`, `validation_memory.py` | Check whether candidates are supported by evidence, equivalent to known good answers, or contradicted by negative signals. |
| Ranking and safety | `frontier.py`, `score_boundaries.py`, `score_tomography.py`, `selector_calibration.py`, `calibration.py`, `probe_feedback.py`, `answer_outliers.py`, `boundary_analysis.py` | Rank improvement frontiers, compare against previous submissions, and block unsafe probes. |
| Reporting | `reports.py`, `coverage_gaps.py`, `contract_gaps.py`, `path_frontier.py`, `pipeline.py` | Explain gaps, opportunities, score limits, and current pipeline readiness. |

## Typical Workflow

Build or refresh graph evidence for the target split, then run candidate
selection and submission-safety checks:

```bash
uv run --no-sync python -m tests.benchmarks.realrca_graph.cli frontier \
  --graph-profile latest-test \
  --baseline .bench-results/realrca-dma/results-test-best8485-gselect-21f8.json \
  --leaderboard .bench-results/realrca-dma/leaderboard-test-20260829-live-refresh-v372.json \
  --tomography .bench-results/realrca-graph/score-tomography-v374-priority-restore.json \
  --team-name 隐元玩一玩 \
  --reference-agent-name probe-gselect-21f8 \
  --out-json .bench-results/realrca-graph/frontier.json \
  --out-md .bench-results/realrca-graph/frontier.md
```

```bash
uv run --no-sync python -m tests.benchmarks.realrca_graph.cli score-boundaries \
  --baseline .bench-results/realrca-dma/results-test-best8485-gselect-21f8.json \
  --frontier .bench-results/realrca-graph/frontier.json \
  --tomography .bench-results/realrca-graph/score-tomography-v374-priority-restore.json \
  --answer-outlier .bench-results/realrca-graph/answer-outliers-v444-custom-monitor.json \
  --out-json .bench-results/realrca-graph/score-boundaries.json \
  --out-md .bench-results/realrca-graph/score-boundaries.md
```

```bash
uv run --no-sync python -m tests.benchmarks.realrca_graph.cli pipeline-status \
  --leaderboard .bench-results/realrca-dma/leaderboard-test-20260829-live-refresh-v372.json \
  --baseline .bench-results/realrca-dma/results-test-best8485-gselect-21f8.json \
  --selector-audit .bench-results/realrca-graph/select-v489-runtime-profile-audit.json \
  --score-boundary .bench-results/realrca-graph/score-boundaries.json \
  --tomography .bench-results/realrca-graph/score-tomography-v374-priority-restore.json \
  --target-accuracy 90 \
  --out-json .bench-results/realrca-graph/pipeline-status.json \
  --out-md .bench-results/realrca-graph/pipeline-status.md
```

## Submission Rule

Treat `pipeline-status` as the final local gate before benchmark submission.
When it reports `ready_to_submit=False`, keep the current best answer file and
mine new evidence or root-boundary explanations instead of submitting another
candidate. This avoids burning leaderboard attempts on cases already known to
have large negative probe deltas.

## Validation

Run the focused graph harness suite after changing this package:

```bash
uv run --no-sync python -m pytest tests/benchmarks/realrca_graph/tests -q
```
