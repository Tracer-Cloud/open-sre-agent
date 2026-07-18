# Adding Tools & Integrations — Definition of Done

Use this checklist whenever you add or materially change:

- a tool — under `integrations/<vendor>/tools/` for a single-vendor tool, or `tools/system/` / `tools/cross_vendor/` for a cross-cutting one (see [tool-placement-policy.md](tool-placement-policy.md))
- an integration under `integrations/<name>/` — its config, client, verifier, and tools
- investigation source wiring for an existing tool or integration

This is the detailed definition of done; use it with [AGENTS.md](../AGENTS.md) and [CI.md](../CI.md).

## 1. Tool checklist

### Files usually involved

- `integrations/<vendor>/tools/<tool_name>_tool/__init__.py` — the tool package (most common path: the tool belongs to a vendor integration)
- `tools/system/<tool_name>/` or `tools/cross_vendor/<tool_name>/` — only when the tool is not vendor-specific (e.g. `tools/system/sre_guidance_tool/`)
- `integrations/<name>/client.py` — reuse a dedicated integration API client instead of inlining requests
- `core/tool_framework/utils/` — shared helper code reused across vendors
- `docs/<tool_name>.mdx` — user-facing usage, parameters, examples
- `tests/tools/test_<tool_name>.py` — behavior and regression coverage

Tools are registry-discovered from **both** `tools/` and `integrations/<vendor>/tools/`, so placement is about ownership, not discovery — see [tool-placement-policy.md](tool-placement-policy.md). Wherever a tool lives, it calls integration-local clients/helpers rather than inlining transport, and never lives in a top-level `vendors/` or `services/` package.

Tool packages must be substantive production modules — no empty or discovery-only `__init__.py`, no thin wrapper that only satisfies registry import. Any tool with validation, credential/parameter resolution, transport/client calls, output normalization, or error handling should split those concerns into focused sibling files (`tool.py`, `models.py`, `validation.py`, `delivery.py`/`client.py`, `results.py`), leaving `__init__.py` as a small registry entrypoint that imports the public tool object.

### Contract and implementation

- [ ] Pick the simplest shape that fits (`@tool(...)` for lightweight tools, a richer class only when needed)
- [ ] `__init__.py` is a small registry entrypoint; non-trivial tools use sibling modules for implementation concerns
- [ ] Metadata is complete and accurate: `name`, `description`, `source`, `surfaces`, `requires`, and any `use_cases` / `outputs` / `retrieval_controls`
- [ ] `input_schema` matches the actual runtime arguments and required fields
- [ ] `is_available` returns `True` only when the tool can genuinely run
- [ ] `extract_params` maps resolved integration state into tool args correctly
- [ ] Validation, credential/parameter resolution, transport/client calls, and result formatting are separated so each can be tested independently
- [ ] Reusable transport or integration-specific parsing lives in `integrations/<name>/` or `core/tool_framework/utils/`, not copied into the tool body
- [ ] Failure responses have a stable, investigation-friendly shape; expected external failures (missing config, auth, rate limit, upstream 4xx/5xx) return structured errors rather than raising — unexpected exceptions use the global `BaseTool` wrapper intentionally or are migrated with telemetry coverage
- [ ] Output is normalized enough for the planner/LLM to consume reliably
- [ ] Secrets never leak through `extract_params`, return values, logs, or traceable tool-call kwargs; secret/PII output is run through `platform/masking/` before return
- [ ] External side effects declare `side_effect_level`, `requires_approval`, and `approval_reason` where appropriate
- [ ] To appear in both investigation and chat, set `surfaces=("investigation", "chat")`

### Live payload parsing

If the tool parses API, MCP, log, or webhook payloads:

- [ ] Validate against the real or documented upstream response shape, not only idealized mocks
- [ ] Handle alternate field names used in live payloads
- [ ] Handle missing or partial fields without returning unusable output
- [ ] Preserve important context when truncating, tailing, paginating, or flattening data
- [ ] Upstream 429 / 5xx responses return a clear, investigation-friendly error rather than raising
- [ ] Add at least one regression test using a realistic fixture payload

Common failure modes to consider: grouped + ungrouped log content; nested/foldered resources; paginated responses; `hasMore` / cursor mismatches; content-vs-pointer shapes (`logs_content` vs `logs_url`-style payloads).

## 2. Integration checklist

### Files usually involved

- `integrations/<name>/__init__.py` — config builders, validators, selectors, normalization helpers
- `integrations/<name>/client.py` — a dedicated API client, when the integration makes direct remote calls
- `integrations/<name>/verifier.py` — local verification logic
- `integrations/<name>/tools/<tool_name>_tool/` — the vendor's agent-callable tools (see §1)
- `integrations/catalog.py` — resolve the integration into the shared runtime config
- `integrations/verify.py` — wire the local verification path
- `docs/<name>.mdx` — user-facing setup, usage, verification
- `tests/integrations/test_<name>.py`, plus `tests/tools/`, `tests/e2e/`, or `tests/synthetic/` where tools or scenarios exercise it

`integrations/<name>/` owns everything about one vendor — config, resolution, clients, verifiers, helpers, **and its tools**. Only vendor-less (`tools/system/`) and cross-vendor (`tools/cross_vendor/`) tools live under top-level `tools/`.

### Examples from the repo

- Datadog: `integrations/datadog/` (with `integrations/datadog/tools/`), `integrations/catalog.py`, tests under `tests/integrations/datadog/` and `tests/tools/test_datadog_*.py`.
- Grafana: `integrations/grafana/` (with `integrations/grafana/tools/`), `integrations/catalog.py`, `surfaces/cli/wizard/local_grafana_stack/`, tests under `tests/integrations/grafana/` and `tests/tools/test_grafana_*.py`.
- Hermes: `integrations/hermes/` (with `integrations/hermes/tools/hermes_logs_tool/` and `.../hermes_session_evidence_tool/`), `surfaces/cli/commands/hermes.py`, `tests/hermes/`, `tests/synthetic/hermes/`.

### Core completeness

- [ ] Config, normalization, and validators are in place under `integrations/<name>/__init__.py`
- [ ] Catalog resolution / env loading is wired correctly
- [ ] Verification path is wired in `integrations/verify.py` and adapters/registry as needed
- [ ] Integration-local client added under `integrations/<name>/client.py` (only if it makes direct remote calls)
- [ ] Tool layer is wired and stable
- [ ] CLI setup flow is updated if the integration is user-configurable locally
- [ ] `opensre onboard` parity is added, or intentionally documented as out of scope
- [ ] New required env vars / credentials are added to `.env.example` (never `.env`)
- [ ] Sensitive credentials follow the [Credential resolution](#credential-resolution) contract below
- [ ] `make verify-integrations` passes

### Credential resolution

Keyring-eligible secrets and non-keyring config follow different write/read paths.
Keep this contract when adding or changing an integration.

| Surface | Write (wizard / setup) | Read (runtime) |
| --- | --- | --- |
| Integration store (`~/.opensre/integrations.json`) | Always on setup | First (preferred) |
| OS keyring | Keyring-eligible secrets via `sync_env_secret` | Via `resolve_env_credential` when env is unset |
| `.env` / process env | Non-keyring config (`*_URL`, user ids, channels, …) | Plain `os.getenv` for that tier |

**Hard rules for new code**

- Never use bare `os.getenv` for a keyring-eligible secret env name (`*_TOKEN`, `*_KEY`, `*_PASSWORD`, `*_SECRET`, connection strings, and similar). Use `resolve_env_credential` from `config.llm_credentials` (env first, then keyring).
- Webhook / `*_URL` values are **never** keyring-backed (wizard routes them to store/`.env`, not `sync_env_secret`). Read with store → plain `os.getenv` only. Webhook URLs often **embed** a secret token — treat them like passwords for logging/masking, even though they are not keyring-eligible.
- Leave `load_env_integration_services` plain-env-only (startup-safe; no keyring at boot).
- Store still wins in `resolve_effective` / merge — env/keyring is the fallback tier only.
- Set `OPENSRE_DISABLE_KEYRING=1` to skip keyring reads/writes (env and store still work).

Canonical helpers: `resolve_env_credential` (env → keyring), `sync_env_secret` / `save_keyring_secret` (keyring-eligible writes), `sync_env_values` (non-keyring `.env` keys only).

## 3. Investigation wiring

If the tool/integration is relevant to investigations:

- [ ] Review alert-source seeding in `core/domain/alerts/alert_source.py`
- [ ] Review source-priority/prompt mapping in `tools/investigation/stages/gather_evidence/prompt.py`
- [ ] Review evidence/source registration in `core/domain/types/` or related state models
- [ ] Add scenario coverage proving the tool surfaces useful RCA evidence

If the integration is first-class for an `alert_source`, review the source-to-tool maps explicitly.

## 4. Discovery and edge cases

For tools that list, search, or inspect resources:

- [ ] Folder/nested resource layouts are considered where the upstream supports them
- [ ] Large result sets are capped or paginated intentionally
- [ ] Partial fetches are surfaced clearly (`truncated`, `fetch_error`, etc.)
- [ ] Time/order-sensitive results preserve causal ordering where it matters

## 5. Docs and tests

### Docs

- [ ] Ship or update a `docs/` page/section in the same PR (new tool, CLI command, pipeline behavior, or integration; and whenever a tool's API/schema or an integration's setup changes)
- [ ] Any new `docs/` page is registered in `docs/docs.json` (without the `.mdx` suffix) so Mintlify navigation shows it
- [ ] Investigation LLM tool-calling changes follow [investigation-tool-calling.md](investigation-tool-calling.md)

### Tests

- [ ] Unit tests for config/normalization
- [ ] Tool contract tests, or equivalent schema/metadata coverage
- [ ] A registry/discovery test proves the tool is visible on the expected surface(s)
- [ ] Runtime behavior tests for success and failure paths
- [ ] At least one realistic fixture for live-payload parsing when external payloads are involved
- [ ] If investigation-relevant, a test proves the planner/agent can discover or invoke the tool through the normal runtime path (plus synthetic/scenario coverage when the loop depends on it)
- [ ] `tests/integrations/` updated when integration wiring changes

Green tests are not enough if they only cover idealized mocks.

### Final gate (new integrations)

Everything above is complete, **and**:

- [ ] Screenshot or demo GIF showing the integration working end-to-end
- [ ] E2E or synthetic test added
- [ ] CI checks pass (see [CI.md](../CI.md))

## 6. Reviewer focus

Before opening or approving the PR, confirm the items most often missed are handled **explicitly**: tool placement (§1), live-payload robustness (§1), alert-source maps (§3), onboarding/setup/docs parity (§2 and §5), pagination/truncation/partial-response behavior (§4), and tests that cover realistic payloads and investigation usefulness — not only happy-path mocks (§5).

Follow [CI.md](../CI.md) for the mandatory pre-push commands.
