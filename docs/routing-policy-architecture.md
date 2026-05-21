# Routing Policy Architecture (ADR)

## Status
Accepted — May 21, 2026.

## Context
The interactive-shell routing policy had grown through layered heuristics in single modules. Rule precedence was implicit in code order, postprocessing mixed normalization with fail-closed policy checks, and backward-compat tuple handling leaked into orchestration paths.

## Decision
1. Deterministic mapping is split into declarative rule packs with one explicit precedence table.
2. Rule matching windows are named typed strategies instead of inline numeric slices.
3. Planner postprocessing runs as pure transforms over a typed `PlannerState`.
4. Fail-closed policy transforms and normalization transforms are registered separately and executed in one ordered list.
5. Legacy planner-result tuple compatibility is collapsed behind a single adapter (`planner_result_adapter.py`).
6. Routing contracts include policy-trace artifacts to detect silent precedence drift.

## Precedence Model
Deterministic mapper precedence is declared in `RULE_PRECEDENCE` in:

`app/cli/interactive_shell/routing/handle_message_with_agent/orchestration/slash_commands/mapper_runner.py`

Current order:
1. `synthetic_suite`
2. `registry_commands`
3. `integration_details`
4. `fallback_provider_switch`
5. `fallback_sample_alert`
6. `fallback_investigation`
7. `fallback_implementation`
8. `fallback_task_cancel`
9. `fallback_shell`

## Extension Guide
When adding a new routing rule or transform:
1. Add rule/transform implementation in the appropriate module (`rule_sets/*` or `postprocessing.py`).
2. Add one explicit entry to the precedence/transform list.
3. Add/adjust contract fixtures in:
   - `app/cli/interactive_shell/routing/tests/contracts/policy_contracts.yml`
4. Add invariants or behavior tests for ordering, dedupe, and fail-closed behavior.
5. Ensure complexity guardrails continue to pass.

## New Rule Checklist
- [ ] Rule has a clear typed contract (input/output and side effects).
- [ ] Rule is registered in the explicit precedence table.
- [ ] Policy trace fixture updated with expected rule hit(s).
- [ ] Golden mapping/postprocessing contracts updated.
- [ ] Invariant tests cover order and fail-closed behavior.
- [ ] Complexity guardrail test still passes.
