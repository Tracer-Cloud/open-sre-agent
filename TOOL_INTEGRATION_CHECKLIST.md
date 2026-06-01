# Tool & Integration Definition of Done

Use this checklist whenever you add or materially change:

- a tool under `app/tools/`
- an integration under `app/integrations/`
- a service client under `app/services/` that changes investigation behavior
- investigation source wiring for an existing tool/integration

This file is the detailed definition of done for tool and integration work. Use it together with [AGENTS.md](AGENTS.md) and [CI.md](CI.md).

## 1. Tool checklist

### Files usually involved

- `app/tools/<ToolName>/__init__.py` or `app/tools/<tool_file>.py`
- `app/tools/utils/` for shared helpers
- `app/services/<vendor>/client.py` if transport/parsing should live in a reusable client
- `docs/<tool_name>.mdx`
- `docs/docs.json`
- `tests/tools/test_<tool_name>.py`

### Contract and implementation

- [ ] Pick the simplest shape that fits the tool (`@tool(...)` for lightweight tools, richer class only when needed)
- [ ] Metadata is complete and accurate: `name`, `description`, `source`, `surfaces`, `requires`, and any `use_cases` / `outputs`
- [ ] `input_schema` matches the actual runtime arguments and required fields
- [ ] `is_available` only returns `True` when the tool can genuinely run
- [ ] `extract_params` maps resolved integration state into tool args correctly
- [ ] Failure responses have a stable, investigation-friendly shape
- [ ] Tool output is normalized enough for the planner/LLM to consume reliably
- [ ] Reusable transport/parsing logic lives in `app/services/` or `app/tools/utils/` rather than being copied into the tool body
- [ ] If the tool should appear in both investigation and chat, set `surfaces=("investigation", "chat")`

### Live payload parsing

If the tool parses API, MCP, log, or webhook payloads:

- [ ] Validate against the real or documented upstream response shape, not only idealized mocks
- [ ] Handle alternate field names used in live payloads
- [ ] Handle missing or partial fields without returning unusable output
- [ ] Preserve important context when truncating, tailing, paginating, or flattening data
- [ ] Add at least one regression test using a realistic fixture payload

Common failure modes to consider:

- grouped + ungrouped log content
- nested/foldered resources
- paginated responses
- `hasMore` / cursor mismatches
- content-vs-pointer response shapes (`logs_content` vs `logs_url`-style payloads)

## 2. Integration checklist

### Files usually involved

- `app/integrations/<name>.py`
- `app/integrations/catalog.py`
- `app/integrations/verify.py`
- `app/services/<name>/client.py`
- `app/tools/<Name>Tool/` or `app/tools/<tool_file>.py`
- `docs/<name>.mdx`
- `docs/docs.json`
- `tests/integrations/test_<name>.py`
- related `tests/tools/`, `tests/e2e/`, or `tests/synthetic/` coverage

### Core completeness

- [ ] Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape
- [ ] Catalog resolution / env loading is wired correctly
- [ ] Verification path is wired in `app/integrations/verify.py` and adapters/registry as needed
- [ ] Add or update the service client only when the integration needs direct remote calls
- [ ] Wire the tool layer after the config path is stable
- [ ] CLI setup flow is updated if the integration is user-configurable locally
- [ ] `opensre onboard` parity is added or intentionally documented as out of scope
- [ ] Add docs and tests together so the integration is understandable and verifiable
- [ ] If a new `docs/` page is added, register it in `docs/docs.json`
- [ ] Run `make verify-integrations` before treating the integration as complete

## 3. Investigation wiring checklist

If the tool/integration is relevant to investigations:

- [ ] Review alert-source seeding in `app/agent/investigation.py`
- [ ] Review source-priority/prompt mapping in `app/agent/prompt.py`
- [ ] Review evidence/source registration in `app/types/` or related state models when relevant
- [ ] Add scenario coverage proving the tool surfaces useful RCA evidence

If the integration is first-class for an `alert_source`, the source-to-tool maps must be reviewed explicitly.

## 4. Discovery and edge cases

For tools that list, search, or inspect resources:

- [ ] Folder/nested resource layouts are considered where the upstream system supports them
- [ ] Large result sets are capped or paginated intentionally
- [ ] Partial fetches are surfaced clearly (`truncated`, `fetch_error`, etc.)
- [ ] Time/order-sensitive results preserve causal ordering where it matters

## 5. Docs, tests, and demos

### Docs

- [ ] If a new feature is shipped (tool, CLI command, pipeline behavior, integration), add or update a `docs/` page/section in the same PR
- [ ] If a tool's API or schema changes, update docs in the same PR
- [ ] If an integration changes, keep docs and config/setup guidance in sync
- [ ] For investigation LLM tool-calling changes, follow [docs/investigation-tool-calling.md](docs/investigation-tool-calling.md)

### Tests

- [ ] Unit tests for config/normalization
- [ ] Tool contract tests or equivalent schema/metadata coverage
- [ ] Runtime behavior tests for success and failure paths
- [ ] At least one realistic fixture for live payload parsing if external payloads are involved
- [ ] Synthetic or scenario coverage when the planner/investigation loop depends on the tool
- [ ] Update `tests/integrations/` when integration wiring changes

Green tests are not enough if they only cover idealized mocks.

### Demo / proof

For a new integration, a PR is only ready when it includes:

- [ ] Integration code added under `app/integrations/<name>/`
- [ ] Tool(s) added under `app/tools/` with proper typing
- [ ] Unit/mock tests added under `tests/integrations/`
- [ ] Docs added under `docs/` and registered in `docs/docs.json`
- [ ] Screenshot or demo GIF showing the integration working
- [ ] E2E or synthetic test added
- [ ] `make verify-integrations` passes
- [ ] CI checks pass (see [CI.md](CI.md))

## 6. PR review checklist

Before opening or approving a PR that adds/changes a tool or integration, confirm:

- [ ] alert-source maps were reviewed explicitly
- [ ] live payload parsing was reviewed explicitly
- [ ] onboarding/setup/docs parity was reviewed explicitly
- [ ] pagination/truncation/partial-response behavior was reviewed explicitly
- [ ] tests cover realistic payloads and investigation usefulness, not only happy paths

Follow [CI.md](CI.md) for the mandatory pre-push commands.
