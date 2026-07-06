# Interactive shell package

These instructions apply to `interactive_shell/` and all of its
subdirectories. The repo-root `AGENTS.md` still applies.

## Purpose

`interactive_shell/` owns the interactive OpenSRE terminal surface: the REPL
loop, slash-command surface, local alert ingestion, shell execution, and Rich /
prompt-toolkit UI. Reusable agent session state, prompt history, grounding, and
prompt construction live under `core.agent`.

Design for a terminal user who may be in the middle of an incident: behavior
should be predictable, interruptible, explainable, and safe by default.

## Package map and ownership

| Area | Owns | Keep out |
| --- | --- | --- |
| `main.py` | process/bootstrap boundary for starting the REPL | per-turn dispatch/runtime logic |
| `controller.py` | top-level REPL wiring, alert listener lifecycle, prompt loop, background workers, and shutdown | feature-specific business logic or compatibility-only forwarding |
| `runtime/core/turn_accounting.py` | shell turn accounting (`ShellTurnAccounting`) for analytics, telemetry, recorder flush, turn persistence, and intent stamps | turn-flow control (owned by `core.agent_harness`) or tool-calling turn execution |
| `command_registry/` | slash-command definitions, argument validation, command dispatch | long-running implementation details better placed in services/runtime modules |
| `runtime/` | background task workers, lifecycle/`ReplState`, runtime context assembly, semantic shell-turn execution, and core harness adapters | prompt text, reusable session persistence, or compatibility shims |
| `tools/interactive_shell/shell/` | shell command parsing, shell execution policy, subprocess execution, and the `run_shell_command`/`run_cd`/`run_pwd` runner (next to the `shell_run` tool in `tools/interactive_shell/actions/shell.py`) | slash-command execution |
| `references/` | CLI/docs/source/AGENTS reference loading and caching | generated model prose |
| `config/` | interactive-shell config loading and tool catalog metadata | global app config unrelated to the REPL |
| `ui/` | Rich/prompt-toolkit rendering, theme, menus, streaming output, and domain views such as `incoming_alerts.py` (receiver/queue/listener lifecycle lives in `core.domain.alerts.inbox`) | business logic or network calls |

When a change crosses these boundaries, prefer extracting a small helper in the
owning area rather than adding more logic to the caller.

## Cross-cutting rules

- Treat every external input as untrusted: user prompt text, slash-command args,
  alert payloads, files read into prompts, history, subprocess output, model
  output, and integration metadata.
- Keep the interactive path responsive. Long-running work must be cancellable,
  timeout-bounded, moved off the input path, or surfaced with clear progress.
- Preserve import-time lightness. Do not start threads, call LLMs, read large
  files, or contact networks at module import time.
- Prefer explicit data models and typed helpers over loosely shaped dictionaries
  when data crosses submodule boundaries.
- Keep user-visible strings intentional. Slash-command names, flags, output
  labels, prompts, response bodies, and error wording are user-facing API.
- Avoid new module-level mutable globals. If global coordination is unavoidable,
  provide deterministic reset/cleanup hooks and test isolation.
- Do not keep compatibility-only forwarding modules after moving code. Migrate
  callers/tests to the canonical owner and remove the old import path in the
  same change.

## Slash commands

- Add commands as `SlashCommand` entries in the relevant `command_registry/*`
  module. Keep handlers small: parse args, call focused helpers, render result.
- **REPL + CLI parity (required):** Every command in `SLASH_COMMANDS` must have a
  matching `_MCP_BY_COMMAND` entry in
  `command_registry/slash_catalog.py`. That catalog feeds the LLM planner (`slash_invoke`),
  planner tool specs, and compact help text. Without it, CI fails
  (`test_slash_catalog_covers_all_registered_commands`).
  - **New REPL-only slash command:** add `SlashCommand` in the owning
    `command_registry/*` module **and** `_mcp(...)` in `slash_catalog.py` (keep
    keys sorted alphabetically in `_MCP_BY_COMMAND`).
  - **New CLI with REPL parity:** add the Click command under `cli/commands/`,
    register a `SlashCommand` in `command_registry/cli_parity.py` (subprocess to
    `opensre …`), **and** add `_MCP_BY_COMMAND` in `slash_catalog.py` with
    `llm_description`, `use_cases`, and `anti_examples` aligned to the command’s
    `usage` tuple.
  - **Verify before push:**
    `uv run python -m pytest tests/interactive_shell/command_registry/test_slash_catalog.py -q`
- Use `validate_args` for cheap pre-policy validation so bad arguments do not
  trigger confirmations or side effects.
- Send command execution through the central dispatch and execution-policy
  helpers. Do not bypass `execution_policy.py` for new commands.
- **Alpha allow-all execution policy (current behavior):** the REPL runs with
  **no command guardrails**. `execution_policy.py` resolves every action to
  `allow` with **no confirmation prompt** — all slash/`opensre` commands,
  investigations, synthetic tests, code-agent launches, LLM runtime switches, and
  **all** shell commands run immediately, in any context (TTY or not, trust mode
  or not). There is **no shell-command safety policy**: the
  read-only/mutating/restricted classification and the `deny` floor were removed
  (`shell_policy.py` deleted; parsing/policy/execution live under
  `tools/shell/`). Mutating commands (`rm`/`mv`/`docker`), `restricted` commands
  (`sudo`, `systemctl`, `kill`, `dd`, …), shell operators (`| && ; > <`), and
  command substitution all run; the `!` prefix is honored but optional. The only
  shell input still rejected is genuinely empty input (a bare `!` or whitespace).
  Do **not** re-add a shell allowlist or deny floor while in alpha — see
  `docs/interactive-shell-action-policy.md`. The former `ExecutionTier`
  classification was removed because it gated nothing under default-allow; if an
  opt-in stricter policy is reintroduced after alpha, gate it in
  `execution_policy.py` (the `ask` verdict, confirmation UX, and `trust_mode` are
  retained as the hook), not via a planner-stage denial.
- Non-TTY behavior under default-allow: actions no longer fail closed on
  non-interactive stdin (there is nothing to confirm). The fail-closed path only
  applies if a verdict is explicitly `ask`, which the default policy does not
  emit.
- **CPR / exclusive-stdin registration (required for table-outputting commands):**
  Under `patch_stdout(raw=True)`, the REPL runs dispatch concurrently with the
  next `prompt_async()`. When a command emits Rich table output, prompt_toolkit
  redraws the prompt mid-flight, sending an `ESC[6n` DSR query; the terminal's
  CPR response (`ESC[<row>;<col>R`) arrives as literal keystrokes in the incoming
  prompt buffer, causing garbage like `^[[60;1R` to appear.
  **Any command that calls `print_repl_table` (directly or via `render_table` /
  `render_integrations_table` / `render_models_table` / etc.) must be added to
  `_EXCLUSIVE_STDIN_MENU_COMMANDS` in `runtime/utils/input_policy.py`.** That makes the main
  loop call `await state.queue.join()`, blocking the next prompt until dispatch
  completes and both drain cycles clean up stale CPR bytes before the next
  `prompt_async()` starts.
  - **How to check:** after adding a command, run it in the REPL and type a few
    characters in the next prompt. If no `^[[…R` garbage appears, the registration
    is correct.
  - **Agent-selected interactive commands:** `_EXCLUSIVE_STDIN_MENU_COMMANDS`
    only reserves stdin for literal `/slash` command text that
    `_literal_slash_command_text` recognizes. When free text like
    "remove github" is resolved by the action agent into an inline-picker
    command (`/integrations remove`, `/integrations setup`, `/mcp connect`,
    `/mcp disconnect`, or a bare `/integrations` / `/mcp` menu), the loop has not
    reserved stdin, so `tools/interactive_shell/actions/slash.py` must NOT run the picker inline. It defers
    via `session.queue_auto_command(...)`, which re-submits the command as
    literal command text so the loop can reserve exclusive stdin before the
    agent path runs it. New raw-stdin picker/wizard commands the action agent can emit
    must be added to
    `_INTERACTIVE_PICKER_MENUS` / `_INTERACTIVE_PICKER_SUBCOMMANDS` in
    `tools/interactive_shell/actions/slash.py`.

## Action Selection And Execution

- **Hard boundary:** do not add regex/keyword/fuzzy intent routing for natural
  language, or any deterministic mapping from non-`/`-prefixed prose to an action
  (e.g. "show integrations" -> `/integrations`). Engineers have been fired before
  for reintroducing intent heuristics that compete with the action agent.
  - **Sanctioned exception:** input the user types as a literal `/slash` command
    is dispatched deterministically (a static `slash_invoke` call in
    `core/agent_harness/turns/action_driver.py`), so slash commands keep working when the
    action-agent LLM is unavailable. This is an explicit-command bypass, not
    intent inference — it fires only when the message *is* a `/command`, and
    free-form text is still LLM-selected. See
    `docs/interactive-shell-action-policy.md` ("Deterministic literal-`/slash`
    dispatch").
- **No planning-stage fail-closed safeguard (v0.1 decision).** The second-phase
  action agent never denies a turn. Because every terminal action is read-only,
  an unmatched/ambiguous/chatty clause is not a safety risk — the agent executes
  the clauses it can map and lets the rest fall through to the conversational
  assistant. We removed the `denied` decision path, the `mark_unhandled` planner
  tool, the `UNHANDLED:` convention, and the "I couldn't safely decide actions"
  message because they caused frequent false denials (e.g. a conversational
  question that embedded a quoted, list-style directive) with no safety upside.
  Details and rationale live in `core/agent_harness/AGENTS.md`. If
  mutating actions are ever introduced, gate them with the
  execution-stage confirmation policy (`tools/shared/execution_policy.py`), not a
  planner-stage denial.
- Keep deterministic command detection in `orchestration/` for terminal UI
  policy only; use the action agent for slash/tool action selection.
- Send uncertainty to a safe surface: help/chat or a clarification, not direct
  mutation or shell execution.
- LLM-generated text must never execute directly. Convert proposed actions into
  explicit planned actions, show them to the user when appropriate, then execute
  through `orchestration/` and policy gates.
- Keep action summaries human-readable and specific enough for confirmation UX
  and audit logs.
- When adding a new action type, test allowed, denied, and confirmation-required
  paths.

## LLM prompts, grounding, and references

- Keep prompts bounded. Enforce size caps for docs, source chunks, histories,
  observations, alert text, and command output included in model context.
- Ground procedural/help answers in maintained references (`docs/`, CLI help,
  AGENTS files, source snippets). If references do not support an answer, say so
  rather than inventing steps.
- Do not include secrets in prompts. Redact or omit tokens, auth headers, env
  values, local credentials, and raw integration config.
- Keep prompt rules reusable in `core.agent_harness.prompts` so chat/help/action
  surfaces use consistent terminology and formatting.
- Reference caches should be deterministic, invalidatable when source files
  change, and cheap to rebuild in tests.

## Terminal UI and rendering

- Escape user-controlled content before passing it to Rich markup
  (`rich.markup.escape`): alerts, command output, file paths, integration names,
  model/provider labels, errors, docs snippets, and model text that is not
  already intentionally rendered as Markdown.
- Use semantic tokens from `ui/theme.py`. Do not introduce raw hex colors, Rich
  named colors, or raw ANSI escapes outside `ui/theme.py` unless a narrow
  prompt-toolkit compatibility path requires it.
- Keep rendering helpers as pure as practical: accept data, return/render Rich
  objects, avoid reading config or mutating session state from UI modules.
- Any raw terminal-mode code must check TTY support and restore terminal state
  in `finally`.
- Be careful mixing `prompt_toolkit.patch_stdout`, Rich live rendering, and
  background output. Prefer append-only, paragraph-buffered, or throttled
  rendering paths that do not corrupt the editable prompt.
- UI changes should handle narrow terminals, non-ASCII fallback where relevant,
  long text, empty states, and non-TTY automation.

## Shell, subprocesses, and local system effects

- Shell execution changes belong under `shell/` and must preserve parsing,
  quoting, timeout, redaction, and policy behavior.
- Treat subprocess output as untrusted display text; escape it before Rich
  markup and cap what is retained or sent to prompts.
- Use explicit timeouts and clear cancellation behavior for subprocesses. Avoid
  waits that can hang the REPL indefinitely.
- Keep allow/deny decisions explainable. If a command is blocked, return a
  user-facing reason and a safe alternative when possible.

## State, history, config, and background work

- Prefer explicit `Session` fields for session state. Keep ownership clear:
  runtime owns lifecycle, history owns persistence, config owns shell-specific
  settings.
- Background threads/tasks/listeners must have deterministic shutdown. Tests
  should stop handles and workers in fixtures or `finally` blocks.
- Protect shared queues and mutable session data with locks or single-owner
  discipline. Avoid check-then-act races around queues, cancellation flags,
  current tasks, and listener handles.
- History should avoid storing secrets or excessive payloads. Apply truncation
  and privacy policy consistently.
- Config loading should degrade gracefully with actionable errors; do not make
  the REPL unusable because an optional config or catalog source is missing.

## External input and local listener safety

- Network-ish local surfaces such as `core.domain.alerts.inbox` (started by the
  REPL entrypoint) must validate cheap request metadata before blocking reads or
  expensive parsing.
- Never perform unbounded request-body reads. For alert POSTs specifically,
  validate `Content-Length` first, and only then read the bounded body:
  - non-numeric `Content-Length` values make `int(...)` raise `ValueError`;
    catch this and return `400`.
  - negative lengths must return `400`; `rfile.read(-1)` reads until EOF rather
    than zero bytes, which can stall the single-threaded handler.
  - oversized positive lengths must return `413` without attempting to read the
    advertised body.
- Preserve clean unauthorized responses for real POST bodies by draining only a
  bounded body before returning `401`; this avoids close-with-unread-data resets
  on some platforms without allowing oversized pre-auth reads.
- Keep request-size and malformed-header checks effective for both authenticated
  and unauthenticated callers.
- Keep non-loopback listener binding protected by a token. Use constant-time
  token comparison and never log bearer tokens, raw auth headers, or full alert
  payloads.

## Testing expectations

- Put tests under `tests/interactive_shell/`, mirroring the package area
  when useful (`orchestration/`, `ui/`, etc.). Never add tests under
  `app/`.
- For focused changes, run the closest tests, for example:
  - `uv run python -m pytest tests/core/domain/alerts/test_inbox.py`
  - `uv run python -m pytest tests/interactive_shell/<area>/`
  - `uv run python -m pytest tests/interactive_shell/`
- Add regression tests for incident-prone edges: platform socket behavior,
  malformed input, non-TTY execution, cancellation, policy denial, prompt-size
  caps, Rich escaping, and background cleanup.
- Prefer deterministic tests over sleep-heavy tests. Use fake classifiers,
  fake sessions, fake consoles, monkeypatched subprocesses, and small fixtures.
- For UI work, test pure formatting/rendering helpers where possible and keep
  full REPL-loop tests minimal.
- For action-planning or execution-policy changes, test both safe fallback behavior and
  the intended positive path.

## Change checklist

Before considering an interactive-shell change complete, check:

1. Is the logic in the right submodule, with import-time side effects avoided?
2. Is user-facing behavior preserved or intentionally documented?
3. Are unsafe actions sent through execution policy with the correct tier?
4. Are external inputs bounded, escaped, redacted, and timeout-protected?
5. Do background resources shut down deterministically?
6. Are focused tests added or updated under `tests/interactive_shell/`?
7. If `SLASH_COMMANDS` changed, does `slash_catalog.py` include every command
   (REPL and `cli_parity`)? Run `test_slash_catalog.py`.
