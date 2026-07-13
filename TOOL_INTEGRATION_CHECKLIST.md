# Tool & Integration Definition of Done

Use this checklist whenever you add or materially change:

- a tool under `tools/`
- an integration under `integrations/`
- an integration-local client or verifier under `integrations/<name>/`
- investigation source wiring for an existing tool/integration

This file is the detailed definition of done for tool and integration work. Use it together with [AGENTS.md](AGENTS.md) and [CI.md](CI.md).

## 1. Tool checklist

### Files usually involved

- `integrations/<vendor>/tools/<tool_name>_tool/__init__.py` for vendor-scoped tools (most common path), or `tools/system/<ToolName>/__init__.py` / `tools/cross_vendor/<ToolName>/__init__.py` for tools that aren't vendor-specific (see [docs/tool-placement-policy.md](docs/tool-placement-policy.md) for the system vs. cross_vendor decision rule)
- `core/tool_framework/utils/` for shared helpers
- `integrations/<name>/client.py` if transport/parsing should live in the integration implementation
- `docs/<tool_name>.mdx`
- `docs/docs.json`
- `tests/tools/test_<tool_name>.py`

`tools/` is the canonical agent-callable boundary. Do not add `@tool(...)`
functions, `BaseTool` classes, or registry-discovered modules under
`integrations/`; tools should call integration-local clients and helpers.

Tool packages must be substantive production modules. Do not add an empty
`tools/<name>/__init__.py`, a discovery-only package, or a thin wrapper that
only satisfies registry import mechanics. Directionally, any tool with
validation, credential or parameter resolution, external transport/client
calls, output normalization, or error handling should split those concerns into
focused sibling files such as `tool.py`, `models.py`, `validation.py`,
`delivery.py`/`client.py`, and `results.py`. In that layout, `__init__.py`
should be a small registry entrypoint that imports the public tool object.

### Contract and implementation

- [ ] Pick the simplest shape that fits the tool (`@tool(...)` for lightweight tools, richer class only when needed)
- [ ] The tool package is not an empty or discovery-only `__init__.py`; non-trivial tools use sibling modules for implementation concerns and keep `__init__.py` as a small registry entrypoint
- [ ] Metadata is complete and accurate: `name`, `description`, `source`, `surfaces`, `requires`, and any `use_cases` / `outputs` / `retrieval_controls`
- [ ] `input_schema` matches the actual runtime arguments and required fields
- [ ] `is_available` only returns `True` when the tool can genuinely run
- [ ] `extract_params` maps resolved integration state into tool args correctly
- [ ] Secrets are not exposed through `extract_params`, return values, logs, or traceable tool-call kwargs
- [ ] Validation, parameter/credential resolution, transport/client calls, and result formatting are separated into focused helpers or sibling files so each can be tested or reasoned about independently
- [ ] Failure responses have a stable, investigation-friendly shape
- [ ] Expected external failures (missing config, auth failure, rate limit, upstream 4xx/5xx) return structured errors; unexpected exceptions either use the global `BaseTool` wrapper intentionally or are migrated with telemetry coverage
- [ ] Tool output is normalized enough for the planner/LLM to consume reliably
- [ ] Reusable transport or integration-specific parsing logic lives in `integrations/<name>/` or `core/tool_framework/utils/` rather than being copied into the tool body
- [ ] External side effects declare `side_effect_level`, `requires_approval`, and `approval_reason` where appropriate
- [ ] If the tool should appear in both investigation and chat, set `surfaces=("investigation", "chat")`
- [ ] Output that may contain secrets, tokens, or PII is run through `platform/masking/` before being returned

### Live payload parsing

If the tool parses API, MCP, log, or webhook payloads:

- [ ] Validate against the real or documented upstream response shape, not only idealized mocks
- [ ] Handle alternate field names used in live payloads
- [ ] Handle missing or partial fields without returning unusable output
- [ ] Preserve important context when truncating, tailing, paginating, or flattening data
- [ ] Upstream 429 / 5xx responses are handled and return a clear, investigation-friendly error rather than raising
- [ ] Add at least one regression test using a realistic fixture payload

Common failure modes to consider:

- grouped + ungrouped log content
- nested/foldered resources
- paginated responses
- `hasMore` / cursor mismatches
- content-vs-pointer response shapes (`logs_content` vs `logs_url`-style payloads)

## 2. Integration checklist

### Files usually involved

- `integrations/<name>/__init__.py`
- `integrations/<name>/client.py`
- `integrations/<name>/verifier.py`
- `integrations/catalog.py`
- `integrations/verify.py`
- `tools/<Name>Tool/` or `tools/<tool_file>.py`, or `integrations/<name>/tools/` when consolidating a vendor's tools under its integration package
- `docs/<name>.mdx`
- `docs/docs.json`
- `tests/integrations/test_<name>.py`
- related `tests/tools/`, `tests/e2e/`, or `tests/synthetic/` coverage

`integrations/` owns user configuration, resolution, clients, verifiers, and
integration-local helpers. `tools/` owns agent-callable behavior. Do not add or
import top-level `vendors/` or `services/` packages.

### Core completeness

- [ ] Integration config, normalization, and validators are in place under `integrations/<name>/__init__.py`
- [ ] Catalog resolution / env loading is wired correctly
- [ ] Verification path is wired in `integrations/verify.py` and adapters/registry as needed
- [ ] Integration-local client is added under `integrations/<name>/client.py` (only if the integration needs direct remote calls)
- [ ] Tool layer is wired and stable
- [ ] CLI setup flow is updated if the integration is user-configurable locally
- [ ] `opensre onboard` parity is added or intentionally documented as out of scope
- [ ] Any new required env vars or credentials are added to `.env.example` (never `.env`)
- [ ] Docs and tests are added together so the integration is understandable and verifiable
- [ ] If a new `docs/` page is added, it is registered in `docs/docs.json`
- [ ] `make verify-integrations` passes

## 3. Investigation wiring checklist

If the tool/integration is relevant to investigations:

- [ ] Review alert-source seeding in `core/domain/alerts/alert_source.py`
- [ ] Review source-priority/prompt mapping in `tools/investigation/stages/gather_evidence/prompt.py`
- [ ] Review evidence/source registration in `core/domain/types/` or related state models when relevant
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
- [ ] Runtime registry/discovery test proves the tool is visible on the expected surface(s)
- [ ] New tool code lives under `tools/`; new integration API client code lives under `integrations/<name>/`
- [ ] Runtime behavior tests for success and failure paths
- [ ] At least one realistic fixture for live payload parsing if external payloads are involved
- [ ] If investigation-relevant, at least one test proves the planner/agent can discover or invoke the tool through the normal runtime path
- [ ] Synthetic or scenario coverage when the planner/investigation loop depends on the tool
- [ ] Update `tests/integrations/` when integration wiring changes

Green tests are not enough if they only cover idealized mocks.

### Final gate (new integrations only)

Before the PR is ready for review, verify all of the above are complete **and**:

- [ ] Screenshot or demo GIF showing the integration working end-to-end
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
