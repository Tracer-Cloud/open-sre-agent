# Memorandum: Turn Scenario Test Infrastructure Gap

**Date:** 2026-06-18  
**Concerns:** `complex_shell_prompts` scenario class; oracle coverage of conversational tool-gathering  
**Status:** Partially addressed (2026-06-26) — gather recording, `tool_actions`, fixture `resolved_integrations`, and `@live` fail-closed CI are in place; many handoff scenarios still rely on text-only contracts

> **Update (2026-06-26):** Natural-language investigation dispatch is re-enabled
> (`INTERACTIVE_SHELL_INVESTIGATION_ENABLED = True`). Scenarios **314**, **338**,
> **339**, and **315** assert gather dispatch via `tool_actions` with fixture
> integrations; **333–335** and **337** use `@live` for canonical per-integration
> gather. Handoff-only **313** lives under `chat_handoff/`. Remaining gap:
> scenarios without `tool_actions` gather entries still pass on hallucination-satisfiable
> text contracts only.

> **Update (2026-06-19):** The scenario schema has since been trimmed and the
> oracle's capability defaults realigned with production. `available_capabilities`
> is now a three-state knob (omit = enabled/production default, `[]` = disabled,
> non-empty = allowlist) instead of disabling slash/cli/synthetic by default, and
> the dead `risk_level`/`tier`/`remote_connected`/`surface` fields were removed.
> See the "Scenario schema and `available_capabilities` semantics" section of
> `core/agent_harness/AGENTS.md` for the canonical contract.

---

## Summary

The turn scenario oracle (`_oracle_runtime.py`) does not observe, assert on,
or control the conversational tool-gathering path (`gather_tool_evidence` →
`Agent.run`). Every `complex_shell_prompts` scenario passes in CI
even when zero integrations are queried and the response is entirely hallucinated
text. The test infrastructure provides confidence that does not exist.

---

## 1. The Two Execution Paths — Only One Is Tested

When a REPL turn enters `run_agent_prompt`, two independent paths
can fire:

| Path | What it does | Oracle coverage |
|---|---|---|
| **Action agent → AgentTool execution** | LLM proposes shell action tool calls (slash, investigation, shell, etc.); the oracle observes the terminal side effects recorded by the action tools | **Fully observed and asserted** |
| **`gather_tool_evidence` → shared runtime loop** | A bounded ReAct loop queries registered tools (Sentry, GitHub, PostHog, etc.) to ground a conversational answer | **Completely unobserved** |

The oracle observes the action-agent execution path. It does not patch
`gather_tool_evidence`, the shared tool-gathering harness, or
`_resolve_session_integrations`.
Tool calls made during the gather pass are invisible to the test.

---

## 2. `configured_integrations` in Fixtures Does Not Isolate the Store

`fresh_session` applies the fixture's `configured_integrations` list to
`session.configured_integrations`. This field controls the LLM system-prompt
copy and the REPL status bar. It does not control which integrations are
**actually loaded** for the gather loop.

`_resolve_session_integrations` ignores `session.configured_integrations`
entirely:

```python
# tools/interactive_shell/tool_gathering.py
def _resolve_session_integrations(session: ReplSession) -> dict[str, Any]:
    if session.resolved_integrations_cache is not None:
        return session.resolved_integrations_cache
    resolved = resolve_integrations({})  # hits the real env and ~/.opensre/integrations.json
    session.resolved_integrations_cache = resolved
    return resolved
```

`resolve_integrations({})` reads the developer's live `~/.opensre/integrations.json`
and environment variables. This produces three distinct, silent behaviours across
environments:

| Environment | What `resolve_integrations` returns | Tool-gathering outcome |
|---|---|---|
| CI (no store, no env keys) | `{}` — no tools available | Gathering is a no-op; text-only answer |
| Developer machine, no keys | `{}` | Same no-op |
| Developer machine with real integrations | Real configs (PostHog, GitHub, Sentry, …) | Real tool calls fire with the developer's live credentials |

A scenario that declares `configured_integrations: [sentry, github, posthog]`
in CI runs exactly the same code path as one that declares
`configured_integrations: []`. The field is decoration, not isolation.

---

## 3. Response Contracts Are Satisfiable by Hallucination Alone

Because the gather loop is a no-op in CI, the response evaluated against the
contract is produced by the LLM from its training data, not from any live
integration. The contracts for the two `complex_shell_prompts` scenarios are:

**313** (`configured_integrations: []`)
```yaml
must_contain_any: [GitHub, issues, Windows, crash]
```
Any response that mentions "GitHub" passes. The LLM always mentions GitHub when
asked about GitHub issues.

**314** (`configured_integrations: [sentry, github, posthog]`)
```yaml
must_contain_all: [Sentry, GitHub, PostHog]
must_contain_any: [Windows, crash]
```
Any response that mentions all three names passes — including a response that
says "I cannot access Sentry, GitHub, or PostHog right now." The scenario
explicitly notes the agent "must commit to checking the connected sources," but
the contract cannot verify this because it cannot observe whether any source
was actually checked.

---

## 4. The Behaviour Proven by Current Tests

Across all 54 turn scenarios, what passes in CI is:

- **Turn-entry correctness** — every turn is handed to the agent entrypoint.
  This is intentionally static; the valuable behavior is downstream dispatch
  and planning.
- **Deterministic command-text detection** — slash commands and aliases resolve
  correctly for UI policy decisions. This is genuine and valuable.
- **Planned terminal action shape** — when a planner action fires (slash, shell,
  investigation), the oracle records and asserts it correctly. This is genuine
  and valuable.
- **Hallucination-satisfiable text contracts** — for all conversational turns,
  the contract is met by the LLM generating plausible text that mentions the
  right words. **This is not a meaningful signal.**

What is not proven:

- Whether the gather loop fires at all.
- Whether any specific tool was called.
- Whether any integration returned data.
- Whether the response is grounded in integration data vs. generated from
  training knowledge.
- Whether a broken integration (validation error, auth failure, timeout)
  prevents the response from being useful.

---

## 5. Why This Is a Large Correctness Risk

The `complex_shell_prompts` class exists specifically to cover the integration
data-gathering surface — the tests are named and described as covering exactly
what they do not cover. This creates three concrete risks:

**Risk 1: Broken tool extraction goes undetected.**  
If `_posthog_mcp_extract_params` starts returning bad config fields (as
happened: live PostHog calls received `posthog_mode="mcp"` from the LLM), the
scenario passes. The broken extraction is only discovered when a user exercises
the feature interactively.

**Risk 2: Integration registration silently drops.**  
If a tool's `is_available` check starts returning `False` for all sessions, or
if the tool is accidentally deregistered, every `complex_shell_prompts` scenario
still passes. A regression that stops the agent from ever querying GitHub or
PostHog cannot be caught by the current test suite.

**Risk 3: The no-mocks policy blocks the obvious fix.**  
`AGENTS.md` and `test_turn_fixture_integrity.py` enforce a hard no-mocks
rule on the turn oracle:

> "Do not use `unittest.mock`, `patch`, `MagicMock`, or equivalent mocking
> primitives in turn tests."

The intent of this rule is correct — it prevents tests from faking the LLM and
making action-planning assertions against synthetic planner output. But it accidentally
also blocks injecting a controlled integration config into the gather loop,
which does not involve the LLM at all. The rule currently prevents the fix.

---

## 6. Root Cause: Architectural Seam Is Missing

The docstring in `scenario_loader.py` acknowledges the gap explicitly:

```python
# Answer docstring, path 2:
# "Deeper 'did it actually query the integration?' assertions belong in
# execution-layer tests, not these turn fixtures."
```

That execution-layer test does not exist. `tests/interactive_shell/runtime/
test_answer_with_tools.py` patches both `gather_tool_evidence` and
`generate_response` entirely, so it tests the wiring between them (gather output
flows to answer), not whether the gather loop calls the right tools with the
right config. The gap noted in the docstring has never been closed.

---

## 7. Proposed Remediation

Three changes are required, in dependency order.

### 7.1 — Add a stable test seam for integration injection

Add `resolved_integrations_override` support to `fresh_session` in the oracle.
When set, `_resolve_session_integrations` returns the override instead of
hitting the real store. This does not mock the LLM, does not mock any tool, and
does not violate the spirit of the no-mocks rule — it controls the integration
config the tool is called with, which is fixture input, not LLM output.

```python
# _oracle_runtime.py
def fresh_session(
    *,
    with_prior_state: bool,
    configured_integrations: tuple[str, ...] = (),
    available_capabilities: dict[str, tuple[str, ...]] | None = None,
    resolved_integrations_override: dict[str, Any] | None = None,
) -> ReplSession:
    session = ReplSession()
    ...
    if resolved_integrations_override is not None:
        session.resolved_integrations_cache = resolved_integrations_override
    return session
```

`run_oracle_once` reads this from `case.scenario.session.resolved_integrations`
when present, and uses `{}` (no-op gather) otherwise. CI remains fast because
no fixture currently sets this field.

### 7.2 — Track gather-loop tool calls in the oracle

Wrap `Agent.run` with a thin recorder inside
`run_oracle_once` so tool calls made during gathering are captured alongside
planned terminal actions. This does not mock the tools themselves; it records
which ones fired.

```python
# _oracle_runtime.py
gathered_calls: list[str] = []

original_run = Agent.run

def _recording_run(self, initial_messages):
    result = original_run(self, initial_messages)
    for tc, _ in result.executed:
        gathered_calls.append(tc.name)
    return result

monkeypatch.setattr(Agent, "run", _recording_run)
```

The oracle result gains `gathered_tool_calls: list[str]` and the OracleRunResult
exposes this for contract assertions.

### 7.3 — Add ``tool_actions`` gather entries to the scenario schema

Fixtures now use a unified ``tool_actions`` list with ``surface: gather`` and
``expect`` modes instead of a separate ``gathered_tools_contract`` block.
Example:

```yaml
tool_actions:
  - surface: gather
    tool: search_sentry_issues
    expect: valid_data
  - surface: gather
    tools: [search_github_issues, list_posthog_tools]
    expect: not_called
```

Extend the YAML schema with an optional section that the scenario loader
validates and the oracle asserts:

```yaml
gathered_tools_contract:
  must_call_any:         # at least one of these tool names must appear
  - list_github_issues
  - search_github_issues
  must_not_call:         # none of these must appear
  - run_investigation
  - execute_shell_command
```

Updated `314-windows-crash-multisource-query.yml`:

```yaml
session:
  configured_integrations: [sentry, github, posthog_mcp]
  resolved_integrations:   # injected into session cache; tool calls run for real
    sentry:
      connection_verified: true
      auth_token: "test-token"
      ...
    github:
      connection_verified: true
      ...
    posthog_mcp:
      connection_verified: true
      mode: streamable-http
      ...

gathered_tools_contract:
  must_call_any:
  - search_sentry_issues
  - list_sentry_issues
  - search_github_issues
  - list_github_issues
  - list_posthog_tools
```

With the override in the session cache, the tools run with the fixture config
(no live credentials needed). With the gather recorder active, the contract is
asserted. A broken `is_available` check or bad `extract_params` now fails the
test immediately.

### 7.4 — Update the no-mocks rule scope

Amend the "no mocks" policy in `AGENTS.md` and `test_turn_fixture_integrity.py`
to distinguish between two separate things:

- **Mocking the LLM** — prohibited. Turn oracle must exercise the real LLM.
- **Injecting fixture integration configs** — permitted. This is equivalent to
  providing test credentials and does not involve the LLM.

Add an AST check that specifically permits `monkeypatch.setattr` on
`tool_gathering._resolve_session_integrations` and
`core.agent.Agent.run` while continuing to prohibit `patch`,
`MagicMock`, and LLM client stubs.

### 7.5 — Rename or reclassify misleading existing scenarios

**313** (`configured_integrations: []`) is now under `chat_handoff/` with
`tool_actions` gather `not_called` assertions. It covers the no-integration
handoff path, not live data gathering. **338** and **339** assert gather
`call_any` with fixture `resolved_integrations`.

---

## 8. Migration Path and Priority

| Step | Effort | Risk | Priority |
|---|---|---|---|
| 7.1 — `resolved_integrations_override` seam | Small (20 lines) | Low | **P0** — unblocks everything else |
| 7.2 — gather-loop tool call recorder | Small (30 lines) | Low | **P0** — required for assertions |
| 7.3 — `gathered_tools_contract` schema + assertions | Medium (100 lines) | Low | **P1** — makes contracts meaningful |
| 7.4 — Update no-mocks rule scope | Trivial | None | **P1** — prevents the fix from being reverted |
| 7.5 — Reclassify 313 | Trivial | None | **P2** — clarity, not correctness |
| Write new `complex_shell_prompts` scenarios with fixture configs | Medium | Low | **P1** — actual test coverage |

Items 7.1 and 7.2 can land in one PR. Items 7.3 and 7.4 land together. New
scenario fixtures follow.

---

## 9. What Does Not Change

- The no-mocks policy on the LLM path. The planner, classifier, and
  conversational assistant all continue to hit the real LLM in turn tests.
- The turn-execution oracle still invokes `run_agent_prompt` directly.
- Any existing passing scenario. The `resolved_integrations_override` is opt-in;
  existing scenarios without it keep the current no-op gather behaviour and
  continue to pass.
- CI runtime budget. Fixture-injected integration configs do not make live
  network calls (tools check `is_available` against the resolved dict, not a
  live endpoint), so test time stays flat.

---

## 10. Acceptance Criteria for "Fixed"

1. A scenario in `complex_shell_prompts` with `resolved_integrations` injected
   and `gathered_tools_contract` defined **fails** when the named tools do not
   fire.
2. The same scenario **passes** when the tools fire and return data.
3. Introducing a bug in `_posthog_mcp_extract_params` (e.g. passing
   `mode="mcp"`) causes the affected scenario to **fail** in CI.
4. A tool whose `is_available` is patched to always return `False` causes the
   scenario to **fail** if it is in `gathered_tools_contract.must_call_any`.
5. No existing scenario changes its pass/fail status.
6. CI runtime increases by less than 10 seconds per shard.
