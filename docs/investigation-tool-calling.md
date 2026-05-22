# Investigation tool calling

Contributor guide for the investigation ReAct loop: tool schemas, LLM invoke payloads, and
conversation messages. Applies to **every** provider the agent uses (Anthropic, OpenAI-compatible,
CLI-backed, Bedrock, and future clients)—not one vendor.

## Architecture

The investigation agent does **not** call integration APIs through the LLM. The flow is:

1. **Tools** — `get_registered_tools("investigation")`, filtered with `tool.is_available(...)`.
2. **Schemas** — `llm.tool_schemas(tools)` from `get_agent_llm()` in `app/services/agent_llm_client.py`.
   Each client class shapes schemas for its API (function definitions, tool specs, CLI prompt JSON, etc.).
3. **Invoke** — `llm.invoke(messages, system=..., tools=tool_schemas)`; the model returns tool calls.
4. **Execute** — Tools run locally; results are appended as user/assistant turns the **same** client can read on the next invoke.
5. **Seed path** — Before the loop, `_build_seed_calls` may inject deterministic tool runs; synthetic
   assistant + tool-result messages must match the active client (`app/agent/investigation.py`).

```text
investigation.py  →  get_agent_llm()  →  *AgentClient.tool_schemas / invoke
                    ↓
              app/tools/*  (input_schema, extract_params, run)
```

### Where code lives

| Concern | Location |
| -------- | -------- |
| Provider routing | `app/services/agent_llm_client.py` (`get_agent_llm`, client classes) |
| Chat / non-agent LLM | `app/services/llm_client.py` (separate path—changes here do not fix investigation) |
| Investigation loop & message dispatch | `app/agent/investigation.py` |
| Provider-specific schema/message helpers | Next to the client implementing `tool_schemas()` (strict normalizers live beside that client) |
| Tool definitions | `app/tools/` (`input_schema`, `public_input_schema`) |

When adding a provider, implement **both** `tool_schemas()` and the message shapes `investigation.py`
already branches on (or extend those branches). Do not assume one vendor’s JSON tool format works elsewhere.

## Why bugs are easy to miss

- **JSON Schema draft-07 vs API strictness** — Tool authors often use patterns that validate in draft-07
  (`"type": ["object", "null"]`, `anyOf`, `nullable`, implicit objects, bare `items: {}`). A given
  LLM API may require a **single string** `type`, explicit `items`, and a closed set of keys. Unit
  tests that only check “has properties” miss union `type` arrays.
- **All tools in one request** — Investigation sends **every available** tool schema in a single invoke.
  One invalid schema fails the whole call (HTTP 400, “invalid tools”, etc.) even when the alert never
  uses that tool.
- **Multiple code paths** — Fixes in `llm_client.py`, chat, or routing do not apply to
  `agent_llm_client.py` unless wired there. Provider-specific normalizers must run in `tool_schemas()`
  (or shared helpers the client calls).
- **Contract tests can lag APIs** — Registry-wide schema tests must encode the **strictest** rules your
  shipped adapters enforce. Extend assertions when production shows a new rejection reason.

## Tool `input_schema` (authoring)

When adding or changing tools under `app/tools/`:

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
uv run python -m pytest tests/services/test_investigation_tool_schemas.py -q
```

Shared assertions live in `tests/services/investigation_tool_schema_contract.py`. When you add a
stricter provider adapter, point `test_investigation_tool_schemas.py` at its normalizer and extend
the contract module if the API rejects new patterns. Bedrock-specific unit tests stay in
`tests/services/test_bedrock_converse.py` (no duplicate registry test there).

## Provider adapters (`agent_llm_client.py`)

Each `*AgentClient` should own:

| Responsibility | Notes |
| ---------------- | ----- |
| `tool_schemas(tools)` | Map `RegisteredTool` / `public_input_schema` → API payload. Never pass raw schemas if the API is strict. |
| `invoke(..., tools=...)` | Attach schemas the API expects; handle retries and map errors to `RuntimeError` with actionable text. |
| Message compatibility | Investigation builds history via `_build_synthetic_assistant_tool_call_msg`, `_build_assistant_msg`, `_build_tool_result_messages`—each must match your invoke parser. |

Checklist when adding or changing a client:

- [ ] `tool_schemas` output matches what `invoke` sends (no duplicate or divergent normalization).
- [ ] New JSON Schema patterns in tools → update the adapter normalizer **and** contract tests in the same PR.
- [ ] Serialized payload round-trips like the SDK will send it (e.g. `json.dumps` on the tools list).
- [ ] Validation errors from the API (“missing field type”, “invalid tools”) → treat as schema/adapter bugs first.
- [ ] Throttling / rate limits: align with existing retry policy in sibling clients.

Provider-specific modules (e.g. strict JSON Schema helpers) stay beside the client; keep investigation
logic in `investigation.py` as dispatch only.

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
uv run python -m pytest tests/services/test_investigation_tool_schemas.py -q
uv run python -m pytest tests/services/test_agent_llm_client.py tests/agent/test_investigation.py -q
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

- [app/services/AGENTS.md](../app/services/AGENTS.md) — API provider wiring and env keys
- [app/integrations/llm_cli/AGENTS.md](../app/integrations/llm_cli/AGENTS.md) — subprocess CLI providers
- [AGENTS.md](../AGENTS.md) — repo map and PR checklist
