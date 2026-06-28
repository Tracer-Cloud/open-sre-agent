# Interactive Shell Action Policy (ADR)

## Status
Superseded — Jun 18, 2026. The declarative-rule-pack deterministic mapper and
the regex-based planner postprocessing overrides described in the original
decision have been removed. See "Decision (current): LLM is the sole tool
selector" below. The original decision is retained for historical context.

## Context
The interactive-shell action policy had grown through layered heuristics in
single modules: a regex/keyword deterministic mapper inferred tools from
free-form text, and planner postprocessing rewrote the model's chosen actions
with more regex. These heuristics competed with the LLM and caused
misclassifications (e.g. "investigate a sample test alert?" being treated as an
informational question instead of running the sample alert), and they were a
recurring source of precedence drift.

## Decision (current): The shell action agent is the sole tool selector
1. There is no regex/keyword intent inference. Non-command turns are
   selected entirely by the shell action agent via native tool-calling.
2. Tool selection is driven by the action-agent system prompt
   (`.../llm_context/system_prompt.py`) and the per-tool descriptions
   in the tool catalog (`interactive_shell/tools/*`). Keep both precise — they
   are the only selection signal.
3. The action path does not post-hoc rewrite the model's tool calls. Tool calls
   execute as first-class `AgentTool`s through the shared `core.runtime`
   tool-calling loop; argument shape and availability are enforced by the
   AgentTool runtime contract and per-tool gates.
4. When the action-agent prompt overflows the context window, the turn falls
   through to a conversational reply rather than guessing an action. When the
   action-agent LLM itself is unavailable, the REPL renders and persists a
   failed assistant turn so `/resume` can show the outage.
5. The runtime's literal-`/slash` detection
   (`runtime/utils/input_policy._literal_slash_command_text`) is terminal-UI
   policy only: spinner suppression and exclusive-stdin gating for literal
   `/slash` command text. It must never infer intent from natural language and
   must not become an action execution shortcut.

## What this means for changes
- To change how a phrasing maps to a tool, edit the action-agent system prompt and/or
  the relevant tool description — never add a regex.
- To add a new tool, add it to the tool catalog with a clear, self-describing
  `description` and `input_schema`; the action agent selects it from that text
  and receives it as an AgentTool.
- Live turn scenarios under
  `tests/core/agent/scenarios/` are the regression
  surface for action-agent behavior. Deterministic scenarios (`intent_class:
  deterministic`) assert literal command dispatch only.

## Original decision (historical, superseded)
1. Deterministic mapping was split into declarative rule packs with one explicit precedence table.
2. Rule matching windows were named typed strategies instead of inline numeric slices.
3. Planner postprocessing ran as pure transforms over a typed `PlannerState`.
4. Fail-closed policy transforms and normalization transforms were registered separately and executed in one ordered list.
5. Legacy planner-result tuple compatibility was collapsed behind a single adapter.
6. Planner contracts included policy-trace artifacts to detect silent precedence drift.

## Integration awareness and LLM-driven read-only discovery

Addendum — Jun 18, 2026.

Factual questions about live state (for example "is sentry installed?") are
answered without adding keyword/regex rules. Two complementary mechanisms:

1. Context grounding (not action planning). At REPL boot, `repl_main`
   (`interactive_shell/entrypoint.py`) hydrates
   `session.configured_integrations` from the shared
   `configured_integration_services()` helper in `integrations/catalog.py`
   (the same source the welcome banner uses, so they never diverge). The chat
   assistant prompt (`build_environment_block` in
   `context/assistant_prompt.py`) lists the configured set as
   facts, letting the model answer directly when state is already known.
2. LLM-driven discovery. The action-agent system prompt
   (`context/action_prompt_text.py`) lets the model, at its own
   discretion, emit a read-only discovery action (for example
   `slash_invoke("/integrations", ["list"])` or `["verify"]`) to discover the
   answer instead of deflecting. There is no keyword mapping for this — the LLM
   decides. Under the alpha allow-all policy every discovery action runs without
   confirmation (`execution_policy.allow_tool("slash")` returns `allow`); the
   former `ExecutionTier`/`resolve_slash_execution_tier` classification was
   removed because it gated nothing. No fail-closed regex rule is involved; the
   action agent decides whether to emit a discovery action.

### Observe→answer summary loop

Addendum — Jun 18, 2026.

When the action agent runs a read-only discovery command to answer a question (e.g.
the user asks "is sentry installed?" and the model runs `/integrations`), the
raw command output (a verification table) is not a direct answer on its own.
The pipeline now follows up with a short assistant pass that summarizes that
output:

1. Read-only discovery slash commands stash a compact text view of what they
   found on `session.agent.last_observation`
   (`_record_integrations_observation` in
   `interactive_shell/command_registry/integrations.py`).
2. `run_agent_prompt` resets that field at the start of every action-agent
   turn and, when a discovery command produced an observation and succeeded,
   calls the conversational assistant with `tool_observation=...`
   (inside the handled-turn observation branch in `pipeline.py`). The assistant
   summarizes the output into a direct answer and is instructed not to emit
   further actions.

This only fires when the action-agent tool path executes a read-only discovery command
and records an observation. The pipeline no longer has a pre-agent deterministic
dispatch branch.

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
command flows through the normal exclusive-stdin turn path of the REPL
(`turn_needs_exclusive_stdin` recognizes `/integrations setup`) — the only
place an interactive child process gets clean stdin. In a non-TTY/scripted
context (no prompt to submit into) the action degrades to telling the user the
command to run.

### Removal of the planning-stage fail-closed safeguard (v0.1)

Addendum — Jun 18, 2026.

The action agent does not deny a turn. Previously, any clause
the old planner could not map to an executable tool — flagged via the `mark_unhandled`
tool, an `UNHANDLED:` text marker, or an unavailable tool call — collapsed the
whole turn into a hard denial that printed *"I couldn't safely decide actions for
that request."* In practice this fired on legitimate input (most often a
conversational question that embedded a quoted, list-style directive such as
*figure out why X is crashing by querying (a) sentry, (b) github, (c) posthog*),
producing a dead end with no safety benefit.

Every terminal action in v0.1 is **read-only**, so an unmatched, ambiguous, or
chatty clause is not a safety risk. The action agent now:

- runs every clause it *can* map to an executable action, and
- lets everything else fall through to the conversational assistant (or simply
  drops a chatty clause in a compound request).

Removed as part of this change: the `denied` field on `ActionPlanningDecision`,
`enforce_plan_fail_closed_policy`, `normalize_terminal_plan`, `render_plan_denied`,
the `mark_unhandled` planner tool, and the `UNHANDLED:` convention. The
`fail_closed`, `has_unhandled_clause`, and `turn.expected_signals` fields were
also removed from turn scenario fixtures, since the oracle never asserted on
them; the fixture `policy` block now carries a single `executes_terminal_action`
`boolean` (true only when a shell action AgentTool is expected to run).

If write/mutating actions are introduced later, gate them with the
execution-stage confirmation policy (`interactive_shell/tools/shared/execution_policy.py`), **not**
an action-selection denial.

### Removal of the shell-command safety policy (alpha)

Addendum — Jun 27, 2026.

**Decision:** while OpenSRE is in **alpha**, the interactive REPL runs **every**
shell command with **no guardrails**. The shell-command safety policy — the
read-only / mutating / restricted classification, the command allowlist, and the
hard `deny` floor — has been removed. This is a deliberate trade-off: alpha
prioritizes developer velocity over command sandboxing, and the REPL already
runs on the developer's own machine with their own privileges.

What changed:

- `shell_policy.py` (classification, allowlists, `classify_command`,
  `evaluate_policy`, `PolicyDecision`) was deleted. The pure parsing helpers it
  also contained moved to `tools/shell/parsing.py` (`parse_shell_command`,
  `argv_for_repl_builtin_detection`, `ParsedShellCommand`), alongside the shell
  execution policy in `tools/shell/policy.py`.
- `tools.shell.policy.evaluate_shell_from_parsed` now returns `allow` for every
  command — read-only, mutating, `restricted` (`sudo`, `systemctl`, `kill`,
  `dd`, …), shell operators (`| && ; > <`), and command substitution
  (`` ` ``/`$(...)`). Commands that need a shell run through one automatically;
  the `!` prefix is still honored but no longer required to escape the old
  operator block.
- The **only** remaining non-execution outcome is genuinely empty input (a bare
  `!` or whitespace), which is rejected as input validation, not as a guardrail.

The `ask`/confirmation machinery (`trust_mode` plus the confirmation UX) is
retained as an unused hook, split across two layers: the pure decision lives in
`interactive_shell/tools/shared/execution_policy.py` (`resolve_confirmation`), and the terminal
interaction (`execution_allowed` — console output, the `Proceed? [Y/n]` prompt,
analytics) lives in `interactive_shell/ui/execution_confirm.py`. If command
guardrails are reintroduced after alpha, gate them here at the execution stage —
never with an action-selection denial in the planner.
