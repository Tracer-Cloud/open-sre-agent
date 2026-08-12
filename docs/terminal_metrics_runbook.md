# Terminal Metrics Runbook

This runbook defines how to interpret the interactive-terminal analytics emitted
by the CLI.

## Event Groups

- Test execution lifecycle: `test_run_started`, `test_run_completed`, `test_run_failed`,
  `test_synthetic_started`, `test_synthetic_completed`, `test_synthetic_failed`
- Interactive terminal behavior: `terminal_actions_planned`, `terminal_actions_executed`,
  `terminal_turn_summarized`

## Core KPIs

- `terminal_action_execution_success_rate`: successful deterministic action executions
- `terminal_fallback_rate`: share of turns that required LLM fallback

## Operational Guidance

- High `terminal_fallback_rate` with low `planned_count` indicates missing deterministic
  action coverage; improve action recognizers before changing LLM prompts.
- High `planned_count` but low execution success suggests command execution reliability
  issues (shell failures, missing dependencies, timeout thresholds).

## Data Contract Source of Truth

- Event enum: `platform/analytics/events.py`
- Capture helpers and KPI query specs: `platform/analytics/cli.py`
- Provider type constraints and coercion: `platform/analytics/provider.py`
