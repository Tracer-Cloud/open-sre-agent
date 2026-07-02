# Investigation tool calling

Contributor guide for the investigation ReAct loop: tool schemas, LLM invoke payloads, and
conversation messages. Applies to **every** provider the agent uses (Anthropic, OpenAI-compatible,
CLI-backed, Bedrock, and future clients)—not one vendor.

## Architecture

The investigation agent does **not** call integration APIs through the LLM. The flow is:

1. **Tools** — `get_registered_tools("investigation")`, filtered with `tool.is_available(...)`.
2. **Schemas** — `llm.tool_schemas(tools)` from `get_agent_llm()` in `core/llm/agent_llm_client.py`.
   Each client class shapes schemas for its API (function definitions, tool specs, CLI prompt JSON, etc.).
3. **Invoke** — `llm.invoke(messages, system=..., tools=tool_schemas)`; the model returns tool calls.
4. **Execute** — Tools run locally; results are appended as user/assistant turns the **same** client can read on the next invoke.
5. **Seed path** — Before the loop, `_build_seed_calls` may inject deterministic tool runs; synthetic
   assistant + tool-result messages must match the active client
   (`tools/investigation/stages/gather_evidence/agent.py`).

```text
investigate/agent.py  →  get_agent_llm()  →  *AgentClient.tool_schemas / invoke
                    ↓
              tools/*  (input_schema, extract_params, run)
```

### Where code lives

| Concern | Location |
| -------- | -------- |
| Provider routing | `core/llm/agent_llm_client.py` (`get_agent_llm`) and `core/llm/llm_client.py` |
| Native SDK clients | `core/llm/sdk/agent_clients.py`, `core/llm/sdk/llm_clients.py` |
| LiteLLM transport | `core/llm/litellm/clients.py`, `core/llm/litellm/routing.py` (when `OPENSRE_LLM_TRANSPORT=litellm`) |
| Chat / non-agent LLM | `core/llm/llm_client.py` (separate path—changes here do not fix investigation) |
| Investigation loop & message dispatch | `tools/investigation/stages/gather_evidence/` and `core/` |
| Provider-specific schema/message helpers | Next to the client implementing `tool_schemas()` (strict normalizers live beside that client) |
| Tool definitions | `tools/` (`input_schema`, `public_input_schema`) |

When adding a provider, implement **both** `tool_schemas()` and the message shapes the runtime loop
already branches on (or extend those branches). Do not assume one vendor’s JSON tool format works elsewhere.

## Why bugs are easy to miss

- **JSON Schema draft-07 vs API strictness** — Tool authors often use patterns that validate in draft-07
  (`"type": ["object", "null"]`, `anyOf`, `nullable`, implicit objects, bare `items: {}`). A given
  LLM API may require a **single string** `type`, explicit `items`, and a closed set of keys. Unit
  tests that only check “has properties” miss union `type` arrays.
- **Many tools in one request** — Investigation sends a **relevance-selected** set of tool schemas in a
  single invoke (`select_investigation_tools` in `tools/investigation/stages/gather_evidence/tools.py`:
  the planner's `planned_actions` when present, otherwise alert-relevant sources first, capped at
  `MAX_AGENT_TOOL_SCHEMAS`). It is still many schemas at once, so one invalid schema can fail the whole
  call (HTTP 400, “invalid tools”, etc.) even when the alert never uses that tool. Tool descriptions and
  parameters live **only** in these schemas — the alert-context user message no longer re-lists them.
- **Multiple code paths** — Fixes in `llm_client.py`, chat, or routing do not apply to
  `agent_llm_client.py` unless wired there. Provider-specific normalizers must run in `tool_schemas()`
  (or shared helpers the client calls).
- **Contract tests can lag APIs** — Registry-wide schema tests must encode the **strictest** rules your
  shipped adapters enforce. Extend assertions when production shows a new rejection reason.

## Tool `input_schema` (authoring)

When adding or changing tools under `tools/`:

- [ ] **Top-level** — Investigation tools use `type: object` with a `properties` dict.
- [ ] **Single `type`** — Prefer one string per node (`"string"`, `"object"`, `"array"`). Avoid
      `"type": ["object", "null"]`; use optional fields via `anyOf`/`oneOf`, omit from `required`, or
      document that a provider adapter will normalize (and add adapter + test in the same PR).
- [ ] **Arrays** — Always set `items` with an explicit `type` or `properties` (never empty `{}`).
- [ ] **Composites** — `$ref`, `$defs`, `allOf`, `anyOf`, `oneOf`, `nullable` may need a normalizer
      in the client adapter; do not add them to public schemas without updating that adapter and tests.
- [ ] **Stability** — Tool call `id` values must stay consistent between the assistant turn that
      requests tools and the following tool-result turn for that provider’s format.

Run tool unit tests under `tests/tools/`. After schema changes, run the registry **strict adapter**
contract (uses the strictest normalizer currently wired in the repo):

```bash
uv run python -m pytest tests/core/runtime/llm/test_investigation_tool_schemas.py -q
```

Shared assertions live in `tests/core/runtime/llm/investigation_tool_schema_contract.py`. When you add a
stricter provider adapter, point `test_investigation_tool_schemas.py` at its normalizer and extend
the contract module if the API rejects new patterns. Bedrock-specific unit tests stay in
`tests/core/runtime/llm/test_bedrock_converse.py` (no duplicate registry test there).

## Provider adapters (`agent_llm_client.py`)

Each `*AgentClient` should own:

| Responsibility | Notes |
| ---------------- | ----- |
| `tool_schemas(tools)` | Map `RegisteredTool` / `public_input_schema` → API payload. Never pass raw schemas if the API is strict. |
| `invoke(..., tools=...)` | Attach schemas the API expects; handle retries and map errors to `RuntimeError` with actionable text. |
| Message compatibility | Investigation builds history via `MessageFormatter` (`core.messages`) — `assistant_from_response`, `tool_results_from_execution`, and `synthetic_assistant_tool_call` — each must match your invoke parser. |

Checklist when adding or changing a client:

- [ ] `tool_schemas` output matches what `invoke` sends (no duplicate or divergent normalization).
- [ ] New JSON Schema patterns in tools → update the adapter normalizer **and** contract tests in the same PR.
- [ ] Serialized payload round-trips like the SDK will send it (e.g. `json.dumps` on the tools list).
- [ ] Validation errors from the API (“missing field type”, “invalid tools”) → treat as schema/adapter bugs first.
- [ ] Throttling / rate limits: align with existing retry policy in sibling clients.

Provider-specific modules (e.g. strict JSON Schema helpers) stay beside the client; keep investigation
logic in `investigation.py` as dispatch only.

### LiteLLM transport

Route all API providers through LiteLLM with a global transport switch (no change to
`LLM_PROVIDER`):

```bash
export OPENSRE_LLM_TRANSPORT=litellm
```

When set to `litellm`, both investigation (`get_agent_llm()`) and non-agent LLM calls
(`get_llm_for_reasoning`, `get_llm_for_classification`, `get_llm_for_tools`) use
`core/llm/litellm/clients.py` via `litellm.completion`. Leave unset or set to `sdk` to use
native vendor SDK clients under `core/llm/sdk/`.

Supported providers: `anthropic`, `openai`, `bedrock`, and OpenAI-compatible providers
(`deepseek`, `groq`, `openrouter`, `gemini`, `nvidia`, `minimax`, `ollama`), plus
`azure-openai` (always via LiteLLM). Set the matching API key and model env vars from
`.env.example` as usual. User-facing setup: [LLM Providers](/llm-providers#litellm-transport).

CLI-backed providers (`codex`, `claude-code`, `opencode`, `kimi`, `copilot`, etc.) always use
their subprocess path regardless of this setting.

## Investigation messages (`investigation.py`)

- [ ] **Same `ToolCall.id`** across synthetic seed assistant message, tool results, and evidence keys.
- [ ] **Provider-specific IDs** — Use opaque ids only when the client requires them (e.g. length/format);
      keep stable `seed_{tool.name}` (or equivalent) where history/tests expect predictable ids.
- [ ] **Block vs string content** — Some APIs require content as structured blocks, not raw strings
      (including after guardrails). Match what `invoke` already produced earlier in the thread.
- [ ] **`zip(tool_calls, results, strict=True)`** when pairing calls to results.

Extend `tests/agent/test_investigation.py` when you add a client branch for synthetic/assistant messages.

## Verification

Minimum before merging schema or client changes:

```bash
uv run python -m pytest tests/core/runtime/llm/test_investigation_tool_schemas.py -q
uv run python -m pytest tests/core/runtime/llm/test_agent_llm_client.py tests/agent/test_investigation.py -q
```

When touching a specific provider, also verify end-to-end with that provider configured:

```bash
uv run opensre
# /investigate <fixture.json>   # interactive shell
# or: opensre investigate -i <fixture.json>
```

Use the same `LLM_PROVIDER` / model users report in issues; unit tests alone are not enough for
adapter strictness gaps.

## Related docs

- [core/llm/AGENTS.md](https://github.com/Tracer-Cloud/opensre/blob/main/core/llm/AGENTS.md) — API provider wiring and env keys
- [integrations/llm_cli/AGENTS.md](https://github.com/Tracer-Cloud/opensre/blob/main/integrations/llm_cli/AGENTS.md) — subprocess CLI providers
- [AGENTS.md](https://github.com/Tracer-Cloud/opensre/blob/main/AGENTS.md) — repo map and PR checklist
