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

## Integration awareness and LLM-driven read-only discovery

Addendum — Jun 18, 2026.

Factual questions about live state (for example "is sentry installed?") are
answered without adding keyword/regex rules. Two complementary mechanisms:

1. Context grounding (not routing). At REPL boot, `repl_main`
   (`app/cli/interactive_shell/runtime/entrypoint.py`) hydrates
   `session.configured_integrations` from the shared
   `configured_integration_services()` helper in `app/integrations/catalog.py`
   (the same source the welcome banner uses, so they never diverge). The chat
   assistant prompt (`_build_environment_block` in
   `app/cli/interactive_shell/chat/cli_agent.py`) lists the configured set as
   facts, letting the model answer directly when state is already known.
2. LLM-driven discovery. The planner system prompt
   (`.../llm_action_planner/constants.py`) lets the model, at its own
   discretion, emit a read-only discovery action (for example
   `slash_invoke("/integrations", ["list"])` or `["verify"]`) to discover the
   answer instead of deflecting. There is no keyword mapping for this — the LLM
   decides. Safety is provided by the existing execution-tier policy in
   `execution_policy.py` (`resolve_slash_execution_tier`): `/integrations`
   (list/show) is `SAFE` and auto-runs, while `/integrations verify` is
   `ELEVATED` and prompts for confirmation. No new fail-closed rule was added,
   and the dead `_fail_closed_unconfigured_integration_detail` policy was left
   unchanged (it now activates naturally once `configured_integrations_known`
   is True).

### Observe→answer summary loop

Addendum — Jun 18, 2026.

When the planner runs a read-only discovery command to answer a question (e.g.
the user asks "is sentry installed?" and the model runs `/integrations`), the
raw command output (a verification table) is not a direct answer on its own.
The pipeline now follows up with a short assistant pass that summarizes that
output:

1. Read-only discovery slash commands stash a compact text view of what they
   found on `session.last_command_observation`
   (`_record_integrations_observation` in
   `app/cli/interactive_shell/command_registry/integrations.py`).
2. `handle_message_with_agent` resets that field at the start of every planner
   turn and, when a discovery command produced an observation and succeeded,
   calls the conversational assistant with `tool_observation=...`
   (`_summarize_observation_turn` in `pipeline.py`). The assistant summarizes the
   output into a direct answer and is instructed not to emit further actions.

This only fires for planner-driven turns. A literal `/integrations list` typed by
the user takes the deterministic fast path (which returns before this logic), so
explicit commands are never re-summarized.

Discovery commands also no longer dump validator stack traces into the REPL: a
vendor/config failure during verification (for example a GitHub MCP `401`) is
logged as a one-line warning instead of a full traceback, because
`report_validation_failure` now defaults to `include_traceback=False` while still
capturing the exception to Sentry.

### Auto-launching interactive setup ("can you configure X?")

Addendum — Jun 18, 2026.

When the user asks to configure, connect, set up, or add an integration
("can you configure sentry?", "connect datadog"), the assistant does not just
print a command to copy — it launches the setup wizard for them. The
conversational assistant emits a `run_interactive` action
(`{"action":"run_interactive","command":"/integrations setup <service>"}`, only
`/integrations setup <service>` or `/mcp connect <server>` are allowed). The
model chooses the service; there is no per-vendor hardcoding.

The setup wizard is a child process that needs exclusive stdin, so it cannot run
inline mid-turn (the live prompt is competing for stdin). Instead the action
queues the command via `session.queue_auto_command(...)`, which prefills the next
prompt and marks it for auto-submit. The prompt refresh hook
(`wire_prompt_refresh` in `prompting/prompt_surface.py`) then submits it, so the
command flows through the normal exclusive-stdin dispatch path of the REPL
(`dispatch_needs_exclusive_stdin` recognizes `/integrations setup`) — the only
place an interactive child process gets clean stdin. In a non-TTY/scripted
context (no prompt to submit into) the action degrades to telling the user the
command to run.
