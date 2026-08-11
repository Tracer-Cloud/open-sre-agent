# Plan: New Relic integration

Technical design for [`spec.md`](spec.md). Every choice here references a concrete
precedent in the repo — the goal is zero invented new patterns.

## ⚠️ Operational restriction on the real key used to validate this plan

The account used to validate Phase 0 (T-00/T-01b) is a real account (`sre@arcotech.io`,
account id captured in T-01b). The user authorized **read-only** use of this key — every
query against it is `query { ... }`, never `mutation { ... }`. Any action that would
change state in New Relic (ack/close an incident, mute a policy, create/edit an alert
condition, a deployment marker) **requires explicit manual approval before running**,
with a clear description of what the action would do. This applies both to this
validation phase and to the `NewRelicClient` implemented later — it does not gain a write
method without a separate, explicit ask. This is an operational reinforcement of the
existing scope decision (`spec.md` §3: "Writing to New Relic... This feature is
read-only"), not a new decision.

⚠️ **Data-sensitivity rule:** any data pulled live from this account (account id,
condition/policy/entity names, incident ids, email, the Notion workspace URL) is treated
as sensitive. None of it goes into final code, comments, tests, fixtures, docs, commit
messages, or PR text — only synthetic values that preserve the discovered *shape*. See
[`tasks.md`](tasks.md) header for the cross-phase rule.

## 0. Precedents used

| Aspect | Reference in the repo |
|---|---|
| Integration package layout | `integrations/honeycomb/` (config/client/verifier/setup separated) |
| Alerts + metrics tools | `integrations/datadog/tools/__init__.py` (`query_datadog_monitors`, `query_datadog_metrics`) |
| End-to-end file list | commit `0b59755c` — "add Railway integration" (#4060), 32 files |
| Vendor-first config (not in `config_models.py`) | commit `5813e207` — "move HoneycombIntegrationConfig into vendor-first config module" (#4435) |
| Tool metadata with enums | commit `49adcdd2` — "migrate metadata declarations to enums" (#4767) |

Note: Railway put its config in `integrations/config_models.py`; Honeycomb was **moved
out** of it later (#4435). We follow Honeycomb — `integrations/new_relic/config.py`.

## 1. Naming

| Thing | Value | Justification |
|---|---|---|
| Service key | `new_relic` | `registry.py`'s multi-word convention: `victoria_logs`, `mongodb_atlas`, `azure_sql`, `incident_io` |
| Aliases | `("newrelic", "new relic")` | `service_key()` normalizes the user-facing label |
| Package | `integrations/new_relic/` | Vendor-first |
| Env prefix | `NEW_RELIC_` | The vendor's own convention (`NEW_RELIC_LICENSE_KEY` etc.) |
| Tools | `query_new_relic_alerts`, `query_new_relic_metrics` | Dominant shape `query_<vendor>_<thing>` |
| Evidence source | `"new_relic"` | `EvidenceSource = str` (open, `core/domain/types/evidence.py`) — nothing to extend in core |

## 2. Client contract

`integrations/new_relic/client.py` — `NewRelicClient(config: NewRelicIntegrationConfig)`.

```
@property is_configured -> bool          # api_key and account_id present
probe_access() -> ProbeResult            # used by the verifier
run_nrql(nrql: str, *, timeout: float) -> dict   # single transport
query_incidents(*, since_minutes, priority, entity_name, limit) -> dict
query_metrics(*, nrql, since_minutes, limit) -> dict
```

`run_nrql` is the **only** place that speaks GraphQL. Both tools go through it.

NerdGraph query:

```graphql
{ actor { account(id: <ACCOUNT_ID>) { nrql(query: "<NRQL>") { results metadata { facets } } } } }
```

`ProbeResult` comes from **`integrations/probes.py`**. A same-named class exists in
`surfaces/cli/wizard/probes.py` — importing that one would break the NFR-6 layer boundary
(`integrations/` doesn't import from `surfaces/`) and the import-linter in `make lint`.

`probe_access()` performs two checks in a single call and distinguishes them in the
result (FR-4): identity (`actor { user { name } }`) and account access
(`actor { account(id: N) { name } }`). If the key is invalid → authentication `errors`.
If the key is valid but the account isn't → `account` comes back `null`. Different
messages.

**Mandatory checking order** on every response — GraphQL returns 200 on logical failure:

1. HTTP status (401/403/429/5xx → structured error by class)
2. `payload["errors"]` non-empty → structured error with the vendor message
   **sanitized** (no query echo, which may contain account data)
3. only then navigate `payload["data"]["actor"]["account"]["nrql"]["results"]`

Error return: `{"success": False, "error": <str>, "error_type": <class>}` — the same
shape `HoneycombClient` and `DatadogClient` already use, so the tools can translate it
via `tool_unavailable`.

### Constants verified against the official docs

All in `config/constants/new_relic.py` (NFR-5), not scattered:

```
NEW_RELIC_NRQL_TIMEOUT_SECONDS = 5      # the API's limit, not our choice
NEW_RELIC_NRQL_LIMIT_MAX       = 5_000  # the API's ceiling
NEW_RELIC_DEFAULT_INCIDENT_LIMIT = 100  # our own ceiling, for LLM context (NFR-8)
NEW_RELIC_DEFAULT_WINDOW_MINUTES = 60
```

`error_type` must distinguish **`timeout`** from **`empty`** (NFR-7). The NerdGraph 5 s
timeout is the most likely operational failure for this integration on a large account,
and reporting it as "no alerts found" would make the agent conclude the opposite of the
truth.

### Alerts tool NRQL

```sql
SELECT incidentId, event, priority, title, conditionName, policyName,
       nrqlQuery, threshold, operator, runbookUrl,
       entity.name, entity.guid, muted, openTime, closeTime
FROM NrAiIncident
SINCE <N> minutes ago
LIMIT <cap>
```

⚠️ **`event` has no reliable casing.** The official docs show `'Open'`/`'Close'`
capitalized; the real account tested (2026-08-11) returned lowercase `'open'`/`'close'`.
The two sources disagree — **never compare with a fixed casing**. Use
`LOWER(event) = 'open'` in the NRQL itself, or `.lower()` if the filter happens on the
Python side.

⚠️ **`NrAiIncident` is 1 row per transition, not per incident** (confirmed against a real
account). A resolved incident produces **two rows sharing the same `incidentId`**: one
`event=open` (`closeTime=null`) and one `event=close` (`closeTime` populated). An
incident still open only has the `open` row.

`results.py` pairs rows by `incidentId` — not by `conditionId` + `entity.guid`:

```python
# one row per incidentId; if a "close" row exists, it wins (it has the final state)
# status = "open" if only the open row exists (closeTime is None)
# status = "closed" if the close row exists
```

Repeated `conditionName`/entity with **different** `incidentId`s are distinct firings of
the same condition (flapping) — **do not** collapse, it's real signal, not a duplicate.
`muted` stays flagged, never filtered — deciding to ignore noise is the agent's call, not
the parser's. `operator` and `title` arrive **HTML-entity-encoded** (`"&gt;="` instead of
`">="`) — `results.py` decodes with `html.unescape()` before exposing them. `threshold`
arrives as a JSON string (`"3.0"`) — don't assume a numeric type.

## 3. Files

### 3.1 New — integration

| File | Content |
|---|---|
| `config/constants/new_relic.py` | `NEW_RELIC_API_KEY_ENV`, `NEW_RELIC_ACCOUNT_ID_ENV`, `NEW_RELIC_BASE_URL_ENV`, `NEW_RELIC_INSTANCES_ENV` + `__all__` |
| `integrations/new_relic/config.py` | `DEFAULT_NEW_RELIC_BASE_URL = "https://api.newrelic.com"`; `NewRelicIntegrationConfig(StrictConfigModel)` with `api_key`, `account_id`, `base_url`, `integration_id`; validators `normalize_url(DEFAULT…)` and `normalize_str()` from `integrations/_validators.py` |
| `integrations/new_relic/__init__.py` | `classify(credentials, record_id)` → `(cfg, "new_relic")` only if `api_key and account_id`; failure via `report_classify_failure` |
| `integrations/new_relic/client.py` | §2 |
| `integrations/new_relic/verifier.py` | `verify_new_relic = register_probe_verifier("new_relic", config=NewRelicIntegrationConfig.model_validate, client=NewRelicClient)` |
| `integrations/new_relic/setup.py` | `NEW_RELIC_SETUP = IntegrationSetupSpec(service="new_relic", fields=(api_key secret, account_id, base_url with a default), verify=verify_new_relic)` |

The verifier does **not** need central registration: `integrations/_verifiers_loader.py`
scans `integrations/*/verifier.py` and imports it automatically.

### 3.2 New — tools

```
integrations/new_relic/tools/
  __init__.py                      # thin entrypoint: imports the public objects
  new_relic_alerts_tool/
    __init__.py                    # exports query_new_relic_alerts
    tool.py                        # metadata + run
    results.py                     # NrAiIncident -> normalized shape
  new_relic_metrics_tool/
    __init__.py
    tool.py
    validation.py                  # sanity-checks the NRQL received from the model
```

The checklist (`docs/adding-tools-and-integrations.md` §1) requires `__init__.py` to be a
registry entrypoint, with validation / transport / formatting living in sibling modules.
Honeycomb and Datadog are flattened legacy — **do not** copy the flattening.

Metadata (enums from `core/tool_framework/metadata.py`):

| | alerts | metrics |
|---|---|---|
| `name` | `query_new_relic_alerts` | `query_new_relic_metrics` |
| `source` | `"new_relic"` | `"new_relic"` |
| `evidence_type` | `EvidenceType.EVENTS` | `EvidenceType.METRICS` |
| `side_effect_level` | `READ_ONLY` | `READ_ONLY` |
| `injected_params` | `("api_key", "account_id", "base_url")` | same |

`surfaces=("investigation", "chat")` is **not** a `ToolMetadata` field — it's a ClassVar
on `core/tool_framework/base.py:61` (default `(ToolSurface.INVESTIGATION,)`) and a kwarg
of the decorator / `RegisteredTool`. Without declaring it explicitly, the tool only shows
up in investigation, never in chat.

`is_available(sources)` = `bool(nr.get("connection_verified") and nr.get("account_id"))`.
`connection_verified` is seeded by `availability_view()` in
`core/tool_framework/utils/integration_sources.py:19`.

`extract_params(sources)` reads `sources["new_relic"]` and returns creds + window
defaults. `injected_params` guarantees the verified credential wins over whatever the
model sends.

**The metrics tool's `validation.py`** is the sensitive spot: the NRQL comes from the
LLM. Minimal scope — reject anything that isn't a read and cap `LIMIT`. NRQL has no DML,
so the risk is cost/volume, not writes; but a query with no `LIMIT` and no time clause
can return a huge payload. Inject default `SINCE`/`LIMIT` when absent.

### 3.3 Edits — integration wiring

| File | Edit |
|---|---|
| `config/constants/__init__.py` | re-export the 4 names + `__all__` |
| `integrations/registry.py` | `IntegrationSpec(service="new_relic", aliases=("newrelic","new relic"), has_verifier=True, direct_effective=True, core_verify=True, setup_order=…, verify_order=…)` |
| `integrations/_catalog_impl.py` | import `classify` + `NewRelicIntegrationConfig`; entry in the classifiers map (~L412); env loader block (~L673) with `_parse_instances_env("NEW_RELIC_INSTANCES", "new_relic")` and `resolve_env_credential(NEW_RELIC_API_KEY_ENV)`, copying the Honeycomb block |
| `integrations/catalog.py` | `add("new_relic", _all_env("NEW_RELIC_API_KEY", "NEW_RELIC_ACCOUNT_ID") or _env_is_set("NEW_RELIC_INSTANCES"))` — plain-env only, no keyring at boot. **Datadog** pattern (`_all_env("DD_API_KEY","DD_APP_KEY")`), not Honeycomb: FR-2 requires both fields, so `_any_env` would report "configured" with half a credential |
| `integrations/effective_models.py` | `new_relic: EffectiveIntegrationEntry \| None = None` |
| `integrations/cli.py` | `_setup_new_relic()` → `_run_spec_setup(NEW_RELIC_SETUP)` + entry in the map (~L713) |
| `tools/registry_discovery.py` | `"integrations.new_relic.tools"` (alphabetically ordered list) |
| `.env.example` | `# New Relic` block in the observability section + commented `NEW_RELIC_INSTANCES=`. **Never** `.env` |

`core_verify=True` because New Relic is a primary observability source — same bucket as
grafana/datadog/honeycomb/groundcover in `CORE_VERIFY_SERVICES`.

`setup_order` / `verify_order`: pick free slots at the end of the range, without
renumbering neighbors. **No test pins the full sequence** —
`tests/integrations/test_registry.py:44-45` recomputes the tuple with the module's own
logic, it's tautological. But four real invariants in
`tests/integrations/test_registry_invariants.py` do hold:

1. `test_setup_orders_are_unique` — `setup_order` unique across all specs
2. `test_verify_orders_are_unique` — same for `verify_order`
3. `test_every_verifier_has_a_verify_order` — `has_verifier=True` **requires**
   `verify_order`
4. registry ↔ `integrations/cli.py::_HANDLERS` is bidirectional:
   `test_every_setup_spec_has_handler` (registry → handler) and
   `test_every_cli_handler_is_registered_in_registry` (handler → registry). Declaring
   `setup_order` without the `_HANDLERS` entry **breaks CI**, and vice versa.

### 3.4 Edits — investigation

| File | Edit |
|---|---|
| `integrations/alert_source_catalog.py` | `_ROUTING_TABLE["new_relic"] = routing(("new_relic",), ("new_relic",))`; `_ALIASES_TABLE["new_relic"] = ("new relic","newrelic","nrql","nr alert")` |
| `tools/investigation/stages/intake/node.py` (~L60) | detection line: `- "new_relic" for New Relic or newrelic.com` |
| `tools/investigation/reporting/context/provenance.py` | entry in the tool→source map and a provenance block (label "New Relic", account id, window) |
| `tools/investigation/reporting/context/evidence_catalog.py` | map `"new_relic": "new_relic_alerts"` + `_add_new_relic_*` and a call in the aggregator (~L388) |
| `tools/investigation/alert_templates.py` | `new_relic` template with `alert_source: "new_relic"` |
| `config/constants/investigation.py` | `"new_relic"` in `ALERT_TEMPLATE_CHOICES` |
| `tools/interactive_shell/shared/slash_catalog.py` (~L377) | `/template` help mentions new_relic |

⚠️ **Guardrail:** no regex/keyword routing outside these declarative tables. The
`AGENTS.md` rule (§Footguns, "Action-agent path") forbids ad-hoc intent routing;
`_ALIASES_TABLE` is the sanctioned mechanism.

### 3.5 Edits — onboarding / UI

| File | Edit |
|---|---|
| `surfaces/cli/wizard/configurators/observability.py` | `_configure_new_relic()` = `configure_from_spec(NEW_RELIC_SETUP, title="New Relic")` |
| `surfaces/cli/wizard/_integration_configurators.py` | register in **two** maps (dispatch ~L93 and service key ~L128) |
| `surfaces/cli/wizard/onboard_integrations.py` | `Choice(value="new_relic", label="New Relic", group="Observability", hint="Query alerts and NRQL metrics")` |
| `surfaces/cli/constants.py` | sample alert entry |
| `surfaces/interactive_shell/ui/banner/banner_state.py` | `"new_relic": "New Relic"` |
| `surfaces/interactive_shell/ui/investigation_outcome.py` | `("new_relic", "new_relic")` |
| `surfaces/interactive_shell/command_registry/investigation.py` | template entries (~L519, ~L529, ~L544) |

### 3.6 Docs

- `docs/new_relic.mdx` — where to create the **User key** (not a License key: it's the
  mistake everyone makes), where to find the `account_id`, US vs EU, exact commands, and
  the gotcha that `NrAiIncident` needs alert conditions already configured for there to
  be anything to read. `AGENTS.md` §Docs rule: cut vendor endpoints, internal function
  names, and which credential tier a value lands in.
- `docs/docs.json` — `"new_relic"` in the "Observability and incidents" group (**without**
  the `.mdx` suffix). Forgetting this leaves the page unreachable on Mintlify.
- `README.md:256` — move New Relic from the "wanted" column to the supported list.
- `docs/.../vocabularies/Mintlify/accept.txt` — check whether Vale needs "NRQL" /
  "NerdGraph" added.

## 4. Tests

Aligned with `AGENTS.md` §Tests: a small suite that pins real failure modes, not line
coverage.

| File | What it pins |
|---|---|
| `tests/integrations/new_relic/test_client.py` | **(a)** `200 + errors` → structured error, not success; **(b)** 401 vs unreachable account → distinct messages; **(c)** 429 and 5xx → structured error with no stack trace; **(d)** a realistic NerdGraph `NrAiIncident` fixture parses; **(e)** empty results ≠ error |
| `tests/integrations/new_relic/test_setup.py` | `IntegrationSetupSpec` ↔ env round-trip |
| `tests/integrations/test_registry.py` | spec registered with `has_verifier`/`direct_effective`/`setup_order` (Railway test's mold) |
| `tests/integrations/test_verify.py` | `resolve_effective_integrations` reads New Relic from env |
| `tests/tools/test_new_relic_alerts_tool.py` | `BaseToolContract`; `is_available` requires **both** `connection_verified` **and** `account_id`; `extract_params`; not-configured path; `results.py`: open+close pairing by `incidentId` into a single record with a derived `status`; distinct `incidentId`s of the same condition **don't** collapse (flapping); `event` compared without a fixed casing (real lowercase `'open'` and doc-capitalized `'Open'` must both match); `operator`/`title` decoded from HTML entities; `threshold` parsed from a string |
| `tests/tools/test_new_relic_metrics_tool.py` | same + default `SINCE`/`LIMIT` injection |
| `tests/tools/test_registry.py` | the real registry discovers both tools |
| `tests/tools/test_telemetry.py` | tool names in the telemetry list |
| `tests/tools/conftest.py` | `sources["new_relic"]` fixture |

**Per-service parametrized test tables the original plan overlooked.** These are
*curated* lists (`_SPECS` / `_CASES` / literal tables), not derived from
`SUPPORTED_SETUP_SERVICES` — omitting New Relic **doesn't break CI**, it leaves a silent
gap. Railway was left out of them; Datadog, Honeycomb, and Coralogix are in. Since New
Relic is `core_verify` observability, it belongs in their group:

| File | What to add |
|---|---|
| `tests/integrations/test_setup_spec_env_round_trip.py` | entry in `_SUBMITTED` (~L78) with **non-default** values (EU host, fictitious account id) — a default "round-trips" even in a spec that wrote nothing |
| `tests/integrations/test_cli_spec_setup.py` | import of the setup module, entry in `_ANSWERS` (~L69) and a `pytest.param` in `_CASES` (~L261) |
| `tests/integrations/test_env_multi_instance.py` | case `("NEW_RELIC_INSTANCES", "new_relic", {...})` (~L155) |
| `tests/integrations/test_catalog_silent_fallback_elimination.py` | `_CLASSIFY_PATCH_TARGETS` (~L99) **and** the env table (~L320) — proves a classify failure goes to `report_exception` instead of a silent `(None, None)` |
| `tests/synthetic/` or `tests/e2e/` | the planner discovers and invokes it through the normal path (mandatory gate for a new integration) |

**Do not** write: a "client was called once" wrapper, a redundant success variant, a
pure-vocabulary table.

`AGENTS.md` reminder: `make typecheck` covers only `config core gateway integrations
platform surfaces tools` — **not** `tests/`. Run mypy on the test path manually.

## 5. Execution order

Each phase is independently verifiable. Detail in [`tasks.md`](tasks.md).

```
Phase 0  Pin the real NrAiIncident shape (blocks the parser)    ← highest risk first
Phase 1  Config + constants + client + verifier + setup         → verify passes
Phase 2  Integration wiring (registry/catalog/cli/env)           → integrations show
Phase 3  Tools + registry_discovery                              → registry discovers
Phase 4  Investigation wiring (routing/intake/reporting)          → template runs an RCA
Phase 5  Onboarding + UI                                          → wizard configures
Phase 6  Docs + tests + CI gates                                  → PR ready
```

Phase 0 comes before code deliberately: Decision 2 carries the plan's single highest
risk, and discovering it in Phase 3 would cost the whole parser.

## 6. Plan review (2026-08-11)

The five original uncertain points were verified against the code.

### Resolved

| # | Point | Conclusion |
|---|---|---|
| 2 | Does a test pin the `setup_order`/`verify_order` sequence? | **No.** `test_registry.py:44-45` is tautological. What actually holds are 4 uniqueness/bidirectionality invariants — documented in §3.3. Inserting at the end of the range is safe. |
| 3 | Does multi-instance work with `account_id` in the identity? | **Yes.** `_parse_instances_env` (`_catalog_impl.py:486`) accepts a flat entry or `{"credentials": …}` and returns **one** record with N instances. `account_id` is just another per-instance credentials field — exactly like `dataset` in Honeycomb and `site` in Datadog. |
| 5 | Does correlation degrade gracefully without a provider? | **Yes, gracefully.** `upstream_correlation/registry.py` returns `None` when no builder matches; only Datadog registers (`_ensure_default_builders_registered`, L103). Leaving it out of scope is safe. |

### Partially resolved

| # | Point | Conclusion |
|---|---|---|
| 4 | Which `slash_catalog.py`? | **Both exist.** `tools/interactive_shell/shared/slash_catalog.py:377` has the `/template` help (the §3.4 target). `surfaces/interactive_shell/command_registry/slash_catalog.py` also exists and **has not been inspected** — T-02b still needs to confirm whether it duplicates the template list. |

### Closed in the second round (verification against official docs)

| # | Point | Conclusion |
|---|---|---|
| 1 | Is `NrAiIncident` the right source? | **Yes — verified.** All attributes exist with the assumed spelling. Decision 2 holds and got stronger: `nrqlQuery` bridges the two tools. See `spec.md` §4 Decision 2 and §10 (sources). |

Corrections and discoveries from the second round:

1. **`event` is `Open`/`Close` capitalized** — `WHERE event = 'open'` would return zero
   rows silently. Fixed in §2.
2. **5 s NRQL-via-API timeout** — a new high risk, became NFR-7/NFR-8. It was the most
   dangerous gap in the original plan: nothing in it handled timeout, and the default
   would confuse it with "no alerts."
3. **`nrqlQuery`, `runbookUrl`, `threshold`, `operator`, `muted`** all exist in the same
   record — FR-6 and Decision 4 got stronger, not weaker.
4. **An issue aggregates N incidents and `NrAiIncident` has no issue id** — a real
   limitation, documented and mitigated by pairing, not ignored.

### Errors fixed in this review

1. `catalog.py` used `_any_env`, contradicting FR-2 (which requires both key **and**
   account_id) → switched to `_all_env`, the Datadog pattern.
2. FR-5 justified `secret=True` as "goes to the keyring" — false. `secret` only masks the
   prompt; the tier comes from the env var's name. Fixed in the spec, with the three
   names verified against `config/env_file.py:52-63`.
3. `surfaces` was listed as a `ToolMetadata` field — it isn't; it's a `BaseTool` ClassVar
   / decorator kwarg.
4. Four per-service parametrized test tables were missing from §4.
5. `ProbeResult` has a same-named class in `surfaces/` — importing the wrong one breaks
   NFR-6.

### Third round — live-account validation (2026-08-11)

Ran the T-00/T-01b curl calls against a real, read-only-authorized account. Found two
things neither review round nor the docs caught, both already folded into §2 and
`spec.md` Decision 2 above: `event` casing disagrees between docs (capitalized) and the
real account (lowercase), and `NrAiIncident` rows are per-transition, not per-incident,
which changed the dedup key from `conditionId`+`entity.guid` to `incidentId` pairing. Also
found `operator`/`title` HTML-entity encoding and `threshold` as a JSON string. See T-01b
in `tasks.md` for the full account.
