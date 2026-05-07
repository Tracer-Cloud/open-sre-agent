# Eval + Terminal Metrics Runbook

This runbook defines how to interpret the evaluation-process and interactive-terminal
analytics emitted by the CLI.

## Event Groups

- Evaluation lifecycle: `eval_process_started`, `eval_process_completed`,
  `eval_process_failed`, `eval_process_skipped`, `eval_process_parse_failed`
- Test execution lifecycle: `test_run_started`, `test_run_completed`, `test_run_failed`,
  `test_synthetic_started`, `test_synthetic_completed`, `test_synthetic_failed`
- Interactive terminal behavior: `terminal_actions_planned`, `terminal_actions_executed`,
  `terminal_turn_summarized`

## Core KPIs

- `eval_pass_rate`: ratio of successful evals where `overall_pass=true`
- `eval_latency_p50_ms` / `eval_latency_p95_ms`: latency percentiles from `duration_ms`
- `eval_parse_error_rate`: parser failures as a percentage of total eval completions/failures
- `terminal_action_execution_success_rate`: successful deterministic action executions
- `terminal_fallback_rate`: share of turns that required LLM fallback

## Operational Guidance

- High `eval_parse_error_rate` generally points to malformed judge output.
- Rising `eval_latency_p95_ms` with stable p50 suggests intermittent upstream LLM delays.
- High `terminal_fallback_rate` with low `planned_count` indicates missing deterministic
  action coverage; improve action recognizers before changing LLM prompts.
- High `planned_count` but low execution success suggests command execution reliability
  issues (shell failures, missing dependencies, timeout thresholds).

## Data Contract Source of Truth

- Event enum: `app/analytics/events.py`
- Capture helpers and KPI query specs: `app/analytics/cli.py`
- Provider type constraints and coercion: `app/analytics/provider.py`
