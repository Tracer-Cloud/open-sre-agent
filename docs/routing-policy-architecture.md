# Routing Policy Architecture (ADR)

## Status
Superseded — Jun 18, 2026. The declarative-rule-pack deterministic mapper and
the regex-based planner postprocessing overrides described in the original
decision have been removed. See "Decision (current): LLM is the sole tool
selector" below. The original decision is retained for historical context.

## Context
The interactive-shell routing policy had grown through layered heuristics in
single modules: a regex/keyword deterministic mapper inferred tools from
free-form text, and planner postprocessing rewrote the model's chosen actions
with more regex. These heuristics competed with the LLM and caused
misclassifications (e.g. "investigate a sample test alert?" being treated as an
informational question instead of running the sample alert), and they were a
recurring source of precedence drift.

## Decision (current): LLM is the sole tool selector
1. There is no regex/keyword intent inference in routing. Non-command turns are
   planned entirely by the LLM action planner via native tool-calling.
2. Tool selection is driven by the planner system prompt
   (`.../llm_action_planner/constants.py`) and the per-tool descriptions in the
   tool catalog (`.../orchestration/tools/*`). Keep both precise — they are the
   only selection signal.
3. The planner does not post-hoc rewrite the model's actions. Parsed tool calls
   are validated for argument shape and tool availability
   (`.../llm_action_planner/normalization.py`, `parsing.py`) and otherwise used
   as-is. `finalize_planner_result_with_trace` is a thin pass-through.
4. When the planner LLM is unavailable or the prompt overflows the context
   window, the turn fails closed to an `assistant_handoff` (conversational
   reply) rather than guessing an action.
5. The only deterministic, non-LLM path that remains is *literal* command
   dispatch — `/slash` commands, bare command aliases (typo-tolerant), and
   `opensre investigate` quick-starts — in
   `command_dispatch/detection.py`. That layer must never infer intent from
   natural language (see the hard rule in its module docstring).

## What this means for changes
- To change how a phrasing maps to a tool, edit the planner system prompt and/or
  the relevant tool description — never add a regex.
- To add a new tool, add it to the tool catalog with a clear, self-describing
  `description` and `input_schema`; the planner selects it from that text.
- Live routing scenarios under
  `cli/interactive_shell/routing/tests/scenarios/` are the regression
  surface for planner behavior. Deterministic scenarios (`intent_class:
  deterministic`) assert literal command dispatch only.

## Original decision (historical, superseded)
1. Deterministic mapping was split into declarative rule packs with one explicit precedence table.
2. Rule matching windows were named typed strategies instead of inline numeric slices.
3. Planner postprocessing ran as pure transforms over a typed `PlannerState`.
4. Fail-closed policy transforms and normalization transforms were registered separately and executed in one ordered list.
5. Legacy planner-result tuple compatibility was collapsed behind a single adapter.
6. Routing contracts included policy-trace artifacts to detect silent precedence drift.

## Integration awareness and LLM-driven read-only discovery

Addendum — Jun 18, 2026.

Factual questions about live state (for example "is sentry installed?") are
answered without adding keyword/regex rules. Two complementary mechanisms:

1. Context grounding (not routing). At REPL boot, `repl_main`
   (`cli/interactive_shell/runtime/entrypoint.py`) hydrates
   `session.configured_integrations` from the shared
   `configured_integration_services()` helper in `integrations/catalog.py`
   (the same source the welcome banner uses, so they never diverge). The chat
   assistant prompt (`_build_environment_block` in
   `cli/interactive_shell/chat/cli_agent.py`) lists the configured set as
   facts, letting the model answer directly when state is already known.
2. LLM-driven discovery. The planner system prompt
   (`.../llm_action_planner/constants.py`) lets the model, at its own
   discretion, emit a read-only discovery action (for example
   `slash_invoke("/integrations", ["list"])` or `["verify"]`) to discover the
   answer instead of deflecting. There is no keyword mapping for this — the LLM
   decides. Safety is provided by the existing execution-tier policy in
   `execution_policy.py` (`resolve_slash_execution_tier`): `/integrations`
   (list/show) is `SAFE` and auto-runs, while `/integrations verify` is
   `ELEVATED` and prompts for confirmation. No fail-closed regex rule is
   involved; the planner decides whether to emit a discovery action and the
   execution tier governs safety.

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
   `cli/interactive_shell/command_registry/integrations.py`).
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

### Removal of the planning-stage fail-closed safeguard (v0.1)

Addendum — Jun 18, 2026.

The second-phase action planner no longer denies a turn. Previously, any clause
the planner could not map to an executable tool — flagged via the `mark_unhandled`
tool, an `UNHANDLED:` text marker, or an unavailable tool call — collapsed the
whole turn into a hard denial that printed *"I couldn't safely decide actions for
that request."* In practice this fired on legitimate input (most often a
conversational question that embedded a quoted, list-style directive such as
*figure out why X is crashing by querying (a) sentry, (b) github, (c) posthog*),
producing a dead end with no safety benefit.

Every terminal action in v0.1 is **read-only**, so an unmatched, ambiguous, or
chatty clause is not a safety risk. The planner now:

- runs every clause it *can* map to an executable action, and
- lets everything else fall through to the conversational assistant (or simply
  drops a chatty clause in a compound request).

Removed as part of this change: the `denied` field on `ActionPlanningDecision`,
`enforce_plan_fail_closed_policy` (replaced by `normalize_terminal_plan`, which
only strips `assistant_handoff` markers), `render_plan_denied`, the
`mark_unhandled` planner tool, and the `UNHANDLED:` convention. The
`fail_closed`, `has_unhandled_clause`, and `route.expected_signals` fields were
also removed from routing scenario fixtures, since the oracle never asserted on
them; the fixture `policy` block now carries a single `executes_terminal_action`
boolean (true only when a planned terminal action is expected to run through the
dispatch gate).

If write/mutating actions are introduced later, gate them with the
execution-stage confirmation policy (`orchestration/execution_policy.py`), **not**
a planner-stage denial.
