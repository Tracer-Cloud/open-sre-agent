# Agent context & data stores

> Contributor reference. If you are trying to understand "what state is the agent
> actually looking at right now?", start here.

A single agent turn touches **four** in-memory/on-disk stores. They are *not*
merged into one object — but each has one clear owner and one clear job. This
page says which is which, so you can find and debug context quickly.

## The four stores at a glance

| # | Store | Lives in | Owns (its one job) | Lifecycle owner |
|---|-------|----------|--------------------|-----------------|
| 1 | **`Session`** | `core/agent_harness/session/state.py` | The whole REPL/gateway session: identity, history, integration cache, per-session prefs, background tasks. | `SessionManager` |
| 2 | **`MutableAgentState`** | `core/context/state/agent_state.py` | The **conversation only**: the running `(role, text)` message list + the last tool observation. Held on `Session` as `session.agent`. | `Session` (embeds it) |
| 3 | **`TurnContext`** | `core/agent_harness/models/turn_context.py` | An **immutable snapshot of one turn**, taken at turn start, so prompt building sees a stable view (not mid-mutation session state). | Built fresh per turn |
| 4 | **JSONL session file** | `core/agent_harness/session/storage/jsonl.py` | **Durable history on disk**: turns, tool calls, investigation results, compaction. | `SessionStorage` protocol |

Rule of thumb:
- **Session** = "everything we remember for this process."
- **MutableAgentState** = "the chat transcript" (a slice of Session).
- **TurnContext** = "a photo of the session at the instant this turn began."
- **JSONL** = "the same thing, written to disk so `/resume` and `/trace` work."

## Store 1 — `Session` (the hub)

`Session` is the process-wide session object used by **every** surface (interactive
shell *and* headless gateway — it is not REPL-specific). Its lifecycle
(create / resolve / rotate / close) is owned by `SessionManager`; see
[`core/agent_harness/AGENTS.md`](../core/agent_harness/AGENTS.md).

Cohesive slices have been factored out so `Session` reads as composition, not a
grab-bag: `session.tokens` (`TokenUsage`), `session.metrics` (`TerminalMetrics`),
`session.agent` (`MutableAgentState`, store #2), `session.storage`
(`SessionStorage`, store #4).

## Store 2 — `MutableAgentState` (conversation only)

**What it actually does in production:** holds the conversation transcript and the
last tool observation. That's it. Reached through `session.agent`:

- `session.agent.messages` — the `(role, text)` list (also via the
  `session.cli_agent_messages` compatibility property).
- `session.agent.last_observation` — the last turn's tool/command output (also via
  `session.last_command_observation`).
- `session.agent.clear()` — reset on `/new`.

**Audit result (issue #3434):** `MutableAgentState` is a ~340-line Zustand-style
store with a much larger API — `system_prompt`, `model`, `available_tools`,
`active_tools`, `run_status`, `pending_tool_calls`, `subscribe()`, `snapshot()`,
`begin_run()`, and a family of `set_*` mutators. **In production, none of that
extra API is used** — only `messages` / `last_observation` / `clear()` are.

**Is it wired into the `Agent` class?** **No.** `core/agent.py` does not import or
touch `MutableAgentState`. The `Agent` loop takes its system prompt, tools, and
integrations as explicit constructor arguments (via `AgentConfig` /
`build_agent`), not from `MutableAgentState`. So `MutableAgentState.system_prompt`
is written once at construction and never read by the runtime.

Practical implication: treat `session.agent` as "the transcript." Do not reach for
its `set_model` / `begin_run` / `snapshot` machinery — it is not on any live path.
(Slimming it to the used surface is tracked as a follow-up.)

## Store 3 — `TurnContext` (one-turn snapshot)

Built once per turn via `TurnContext.from_session(text, session)` and passed to the
prompt builders. Because it is an **immutable** snapshot, a prompt reflects
turn-start state even if the session mutates mid-turn.

**There is exactly one `TurnContext` type — no surface-specific variants.** (Issue
#3434 mentioned a `ShellTurnContext`; it does not exist. `ReplRuntimeContext`,
`GroundingContext`, `ActionToolContext`, etc. are unrelated objects, not
`TurnContext` subclasses.) So there is nothing to "consolidate" here — the single
type is already the shared base.

`TurnContext` can also drive `Agent.run(agent_context=...)`: it carries
`system_prompt`, `active_tools`, `resolved_integrations`, and `max_iterations` for
the runtime-request path.

## Store 4 — JSONL session file (durable)

Every turn, tool call, investigation result, and compaction is appended to a
per-session JSONL file through the `SessionStorage` protocol
(`JsonlSessionStorage` in production, `InMemorySessionStorage` in tests). This is
what `/resume` reloads and what `/trace` reads. It is intentionally decoupled from
the in-memory stores: the on-disk format can change without touching `Session`.

## Where prompts are built

System prompts are assembled per surface, then handed to the agent as a plain
string (`AgentConfig.system`). After issue #3434 the builders live in **two**
homes:

| Surface | Builder | Home |
|---------|---------|------|
| Action agent | `build_action_system_prompt` | `core/agent_harness/prompts/` |
| Conversational assistant | `build_assistant_system_prompt` | `core/agent_harness/prompts/` |
| Evidence gather | `build_gather_system_prompt` | `core/agent_harness/prompts/` |
| Investigation pipeline | `build_investigation_system_prompt` | `tools/investigation/stages/gather_evidence/prompt.py` |

Home 1 — **`core/agent_harness/prompts/`** — is the single home for every
harness-surface prompt. Home 2 is the investigation pipeline's own
domain-specific prompt, which lives with the investigation stage that uses it.

## Seeing the final prompt (debuggability)

The **assembled system prompt is captured on every agent turn** (issue #3434,
Problem 1). `core/agent.py::Agent.run(...)` records the exact system prompt sent to
the LLM onto its result — `AgentRunResult.final_system_prompt` — captured *after*
the `_before_provider_request` hook, so it reflects any per-turn edits, not a
pre-hook approximation.

The capture lives in the shared core loop, **not** in any one surface, so every
surface (interactive shell, CLI, gateway/Telegram, headless) records the same
thing from the same place. That answers "what was influencing the agent when it
made this decision?" without re-deriving the prompt by hand.

**Follow-up (not yet wired):** persisting `final_system_prompt` to the per-session
JSONL trace so `/trace` can render it. That belongs in the core turn record (the
shared run-record path), *not* in the shell-only `PromptRecorder` — recording it in
one surface is exactly the per-surface divergence this restructure is removing. The
shell's `PromptRecorder` today records the *user* input text, not the system
prompt, and is a separate, surface-local mechanism.
