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
- [ ] Secrets never leak through `extract_params`, return values, logs, or traceable tool-call kwargs; secret/PII output is run through `infrastructure/safety/masking/` before return
- [ ] External side effects declare `side_effect_level`, `requires_approval`, and `approval_reason` where appropriate
- [ ] To appear in both investigation and chat, set `surfaces=(ToolSurface.INVESTIGATION, ToolSurface.CHAT)`

### Live payload parsing

If the tool parses API, MCP, log, or webhook payloads:

- [ ] Validate against the real or documented upstream response shape, not only idealized mocks
- [ ] Handle alternate field names used in live payloads
- [ ] Handle missing or partial fields without returning unusable output
- [ ] Preserve important context when truncating, tailing, paginating, or flattening data
- [ ] Upstream 429 / 5xx responses return a clear, investigation-friendly error rather than raising
- [ ] Add at least one regression test using a realistic fixture payload

Common failure modes to consider: grouped + ungrouped log content; nested/foldered resources; paginated responses; `hasMore` / cursor mismatches; content-vs-pointer shapes (`logs_content` vs `logs_url`-style payloads).

### Skill guidance (optional)

A tool can carry workflow guidance the model reads on every call by shipping a `SKILL.md`. The guidance is **appended to the tool's `description`** under a `Workflow guidance:` heading — it is permanent schema text, not a side channel. All guidance targeting one tool is combined and truncated at **2400 characters** (`tools/registry_skill_guidance.py`), so budget it like description text: the longer the guidance, the more of every request it consumes.

Skill guidance and a harness playbook are **independent and composable** — a tool may have both. Skill guidance rewrites one tool's description; it does not replace agent- or harness-level guidance for a multi-step flow. The GitHub tools (`github_cli`, `ci_fix`, `security_fix`) each ship a `SKILL.md`; adding one never means removing broader guidance.

**When to add (two independent axes — evaluate both):**

| Axis | Add when | Skip when |
| --- | --- | --- |
| **Tool `SKILL.md`** (this section) | ≥2 of: misuse is expensive; not obvious from the tool `description`; reused across many turns | One clear tool; thin/rare vendor; tip that fits in `description` |
| **Harness playbook** (`core/agent_harness/prompts/skills/*/SKILL.md` + `skill_view`) | Multi-step WHEN / DO NOT, sibling carve-outs, same-turn vs next-turn, or a report template | Single-tool tip with no cross-turn flow |

**Neither** is the default for most of ~70 `tools/` packages. Missing `SKILL.md` is usually correct. **Never** add one-per-vendor stubs, or one skill per observability vendor when the failure mode is shared (query hygiene is one class, not Datadog + Grafana + CloudWatch copies).

Harness authoring template: `core/agent_harness/prompts/skills/_template/SKILL_TEMPLATE.md`.

**File.** A `SKILL.md` with YAML frontmatter and a markdown body:

```yaml
---
name: github-cli          # required — lowercase kebab-case, ≤ 64 chars
description: >            # required — ≤ 1024 chars; the model reads this to decide relevance
  One or two sentences: when to reach for these tools.
tools:                    # required — the registered tool name(s) this guidance applies to
  - github_cli
disable-model-invocation: false   # optional — set true to suppress attachment entirely
---

# Body — markdown workflow guidance shown after the tool's own description.
```

**Register it, or it is silently ignored.** The loader reads only files it is told about:

- [ ] **Explicit:** add the file's path to `_skill_guidance_files()` in `tools/registry_skill_guidance.py`. A `SKILL.md` that exists on disk but is absent from that tuple is **never loaded** — unlisted and missing files are skipped with no diagnostic, the same trap as forgetting a `docs.json` entry.
- [ ] Or place it under `tools/system/python_execution_tool/skills/*/SKILL.md`, which is discovered automatically.

**Check the registry-load logs** — the loader warns and skips rather than failing the build:

- `unknown_tool` — a name under `tools:` matches no registered tool; that target is dropped.
- `invalid_metadata` — missing `name`/`description`/`tools`, an over-long `name`/`description`, or a `name` that is not lowercase kebab-case; the whole skill is skipped.
- `parse_failed` — malformed frontmatter YAML.

## 2. Integration checklist

### Files usually involved

- `integrations/<name>/__init__.py` — config builders, validators, selectors, normalization helpers
- `integrations/<name>/client.py` — a dedicated API client, when the integration makes direct remote calls
- `integrations/<name>/verifier.py` — local verification logic
- `integrations/<name>/tools/<tool_name>_tool/` — the vendor's agent-callable tools (see §1)
- `integrations/<name>/background_adapter.py` — only for messaging integrations you want selectable as a background-RCA completion channel (see [Notification channels](#notification-channels))
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
- [ ] Background-RCA delivery is wired, or intentionally out of scope (see [Notification channels](#notification-channels))
- [ ] `opensre onboard` parity is added, or intentionally documented as out of scope
- [ ] New required env vars / credentials are added to `.env.example` (never `.env`)
- [ ] Sensitive credentials follow the [Credential resolution](#credential-resolution) contract below
- [ ] `make verify-integrations` passes

### Notification channels

Only if the integration should appear in `/background notify set <channel>` as a destination for a completed background RCA. See [background-investigations.mdx](background-investigations.mdx) for the user-facing behaviour.

Add `integrations/<name>/background_adapter.py` with three members:

```python
class _MyChannelBackgroundAdapter:
    name = "mychannel"  # the literal a user types
    capabilities = frozenset(
        {BACKGROUND_RCA}
    )  # from infrastructure.delivery.notifications.outbound_registry

    def deliver(self, record: BackgroundInvestigationRecord) -> str:
        return deliver_mychannel_notification(record)  # module-level function, see below
```

- [ ] Register the adapter object in `bootstrap/adapters.py`. Nothing auto-discovers it, and importing the module is **not** enough: imports are cached, so a re-import after the registry is cleared runs no module body.
- [ ] `deliver` **never raises**. Return `"sent"`, `"failed: <reason>"`, or `"missing <name> integration: <what to configure>"`. The string is persisted on the record and shown by `/background show`, so redact any credential in the reason.
- [ ] Import the vendor client **inside** `deliver`, not at module scope. These modules are imported when a user runs `/background notify set`, so a module-level client lands on that path.
- [ ] Send the bounded summary from `infrastructure.delivery.notifications.rca_summary`, not the full report, for any channel with a message-size limit.

The channel becomes selectable as soon as it is registered — `/background notify set` derives its allowed list from the registry, so there is no channel list to edit.

### Credential resolution

Secret env names and non-secret config follow different write/read paths.
Keep this contract when adding or changing an integration.

| Surface | Write (wizard / setup) | Read (runtime) |
| --- | --- | --- |
| Integration store (`~/.opensre/integrations.json`) | Always on setup | First (preferred) |
| Local credentials file (`~/.opensre/credentials.json`) | Secrets via `sync_env_secret` | Via `resolve_env_credential` when env is unset |
| `.env` / process env | Public config and secrets (`sync_env_values` / `sync_env_secret`) | Plain `os.getenv` for that tier; `resolve_env_credential` reads env first |

**Hard rules for new code**

- Never use bare `os.getenv` for a secret env name (`*_TOKEN`, `*_KEY`, `*_PASSWORD`, `*_SECRET`, connection strings, and similar). Use `resolve_env_credential` from `config.llm_credentials` (env first, then the credentials file).
- Webhook / `*_URL` values are **never** written to the credentials file (wizard routes them to store/`.env`, not `sync_env_secret`). Read with store → plain `os.getenv` only. Webhook URLs often **embed** a secret token — treat them like passwords for logging/masking.
- Leave `load_env_integration_services` plain-env-only (startup-safe; no credentials-file read at boot).
- Store still wins in `resolve_effective` / merge — env/credentials-file is the fallback tier only.
- Tools receive credentials through `extract_params` (resolved integration state), never their own env reads. At execution, keys listed in the tool's `injected_params` override model-supplied values, so the verified source wins even when the model passes a token. A tool's resolver may read env only as the final fallback when nothing was injected — `integrations/github/tools/github_cli/credentials.py` is the reference: explicit/injected token first, then `GITHUB_MCP_AUTH_TOKEN`, then `GITHUB_TOKEN`/`GH_TOKEN`.
- Set `OPENSRE_DISABLE_KEYRING=1` to skip local-file reads/writes (env and store still work).

Canonical helpers: `resolve_env_credential` (env → credentials file), `sync_env_secret` / `save_credential` (secret writes to the credentials file and `.env`), `sync_env_values` (`.env` keys, including secrets).

## 3. Investigation wiring

If the tool/integration is relevant to investigations:

- [ ] Review alert-source seeding in `core/domain/alerts/alert_source.py`
- [ ] Review source-priority/prompt mapping in `tools/investigation/stages/gather_evidence/prompt.py`
- [ ] Review evidence/source registration in `core/domain/types/` or related state models
- [ ] **Declare `@tool(evidence_mapper=...)`** (or set the `evidence_mapper`
  class attribute on a `BaseTool`) if the tool's output should be citeable in the
  report. The mapper lifts the raw output into the canonical report keys the
  evidence catalog cites, and it lives with the tool in its own package — the
  investigation stage stays vendor-agnostic. A tool with no mapper keeps its raw
  output only; the catalog never mints a citeable key for it. The coverage guard
  (`tests/tools/investigation/stages/gather_evidence/test_evidence_mapper_coverage.py`)
  fails until you either add the mapper or record the tool in
  `evidence_mapper_baseline.txt` as a deliberate known gap.
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
