# Interactive shell package

These instructions apply to `app/cli/interactive_shell/` and all of its
subdirectories. The repo-root `AGENTS.md` still applies.

## Purpose

`app/cli/interactive_shell/` owns the interactive OpenSRE terminal: the REPL
loop, slash-command surface, local alert ingestion, LLM-backed help/chat,
action planning, shell execution, session state, history, routing, and Rich /
prompt-toolkit UI.

Design for a terminal user who may be in the middle of an incident: behavior
should be predictable, interruptible, explainable, and safe by default.

## Package map and ownership

| Area | Owns | Keep out |
| --- | --- | --- |
| `loop.py` / `commands.py` | top-level REPL wiring and compatibility shims | feature-specific business logic |
| `command_registry/` | slash-command definitions, argument validation, command dispatch | long-running implementation details better placed in services/runtime modules |
| `runtime/` | `ReplSession`, background tasks, hot reload, lifecycle state | UI rendering and prompt text |
| `intent/` | deterministic parsing and interaction models | LLM calls and command side effects |
| `routing/` | route selection/classification and fallback behavior | direct action execution |
| `orchestration/` | action planning, execution policy, action executor | raw UI formatting and provider-specific clients |
| `shell/` | shell command parsing, allow/deny policy, subprocess execution | slash-command routing |
| `chat/` | assistant/help answer surfaces | direct mutation of runtime state outside the action executor |
| `prompting/` | reusable prompt rules and follow-up wording | docs/source retrieval |
| `references/` | CLI/docs/source/AGENTS reference loading and caching | generated model prose |
| `config/` | interactive-shell config loading and tool catalog metadata | global app config unrelated to the REPL |
| `history/` | REPL history policy and persistence | prompt rendering |
| `ui/` | Rich/prompt-toolkit rendering, theme, menus, streaming output | business logic or network calls |
| `alert_inbox.py` / `alert_renderer.py` | local alert receiver, queue, and alert presentation | general HTTP framework behavior |

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

## Slash commands

- Add commands as `SlashCommand` entries in the relevant `command_registry/*`
  module. Keep handlers small: parse args, call focused helpers, render result.
- Always assign the correct `ExecutionTier`:
  - `EXEMPT`: only meta commands that must never prompt, such as exit/trust
    controls.
  - `SAFE`: read-only, local, low-cost informational commands.
  - `ELEVATED`: mutating, destructive, expensive, networked, verification, or
    process-control commands.
- Use `validate_args` for cheap pre-policy validation so bad arguments do not
  trigger confirmations or side effects.
- Route command execution through the central dispatch and execution-policy
  helpers. Do not bypass `execution_policy.py` for new commands.
- Preserve non-TTY behavior: commands that require confirmation must fail closed
  when stdin is not interactive unless trust mode explicitly allows them.

## Routing, intent, and action execution

- Keep deterministic parsing in `intent/`; use LLM classification only where the
  deterministic rules cannot reasonably decide.
- Route uncertainty to a safe surface: help/chat or a clarification, not direct
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
- Keep prompt rules reusable in `prompting/` so chat/help/action surfaces use
  consistent terminology and formatting.
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

- Prefer explicit `ReplSession` fields for session state. Keep ownership clear:
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

- Network-ish local surfaces such as `alert_inbox.py` must validate cheap request
  metadata before blocking reads or expensive parsing.
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

- Put tests under `tests/cli/interactive_shell/`, mirroring the package area
  when useful (`routing/`, `orchestration/`, `ui/`, etc.). Never add tests under
  `app/`.
- For focused changes, run the closest tests, for example:
  - `uv run python -m pytest tests/cli/interactive_shell/test_alert_inbox.py`
  - `uv run python -m pytest tests/cli/interactive_shell/<area>/`
  - `uv run python -m pytest tests/cli/interactive_shell/`
- Add regression tests for incident-prone edges: platform socket behavior,
  malformed input, non-TTY execution, cancellation, policy denial, prompt-size
  caps, Rich escaping, and background cleanup.
- Prefer deterministic tests over sleep-heavy tests. Use fake classifiers,
  fake sessions, fake consoles, monkeypatched subprocesses, and small fixtures.
- For UI work, test pure formatting/rendering helpers where possible and keep
  full REPL-loop tests minimal.
- For routing or execution-policy changes, test both safe fallback behavior and
  the intended positive path.

## Change checklist

Before considering an interactive-shell change complete, check:

1. Is the logic in the right submodule, with import-time side effects avoided?
2. Is user-facing behavior preserved or intentionally documented?
3. Are unsafe actions routed through execution policy with the correct tier?
4. Are external inputs bounded, escaped, redacted, and timeout-protected?
5. Do background resources shut down deterministically?
6. Are focused tests added or updated under `tests/cli/interactive_shell/`?
