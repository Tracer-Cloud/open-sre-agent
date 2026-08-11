# Tasks: New Relic integration

Execution of [`plan.md`](plan.md). Every task has an objective verification gate —
nothing advances on "looks right."

Convention: `[ ]` pending · `[~]` in progress · `[x]` done with a green gate ·
`[!]` blocked.

⚠️ **Cross-phase rule, all phases:** any data that came from the real test key (account
id, condition/policy/entity names, incident ids, email, the Notion URL) is treated as
sensitive. No real value goes into code, comments, tests, fixtures, docs, commit
messages, or PR text — only synthetic forms that preserve the discovered *shape*. The key
is for local validation only, never committed, never in the repo's `.env`.

---

## Phase 0 — Discovery (blocks the parser)

### [x] T-00 · Get access to a real New Relic account
Done on 2026-08-11. User key confirmed (`data.actor.user` → `sre@arcotech.io`, id
`1004267780`); `account_id` confirmed by T-01b's incidents query returning 20 real
records. **Read-only** use — operational restriction documented at the top of
`plan.md`.

### [x] T-01a · Pin the `NrAiIncident` shape against official docs
Done on 2026-08-11. **All** assumed attributes confirmed; `event` corrected to
`Open`/`Close` capitalized; discovered `nrqlQuery`, `runbookUrl`, `threshold`,
`operator`, `muted`, `closeCause`, `entity.guid`. API limits confirmed (5 s timeout,
`LIMIT MAX` 5,000, 25 concurrent). Sources in `spec.md` §10.
The shape risk is **closed** — Decision 2 holds and got stronger.

### [x] T-01b · Confirm retention and capture a real payload
Done on 2026-08-11 against a real account (`sre@arcotech.io`, **read-only** use — see
the operational restriction at the top of `plan.md`). The `plan.md` §2 NRQL returned 20
incidents within a 30-day window with no error — sufficient retention for RCA confirmed
on that account (exact per-plan retention, via the Data Retention UI, not read — doesn't
block, stays as a docs note). The real payload revealed 3 corrections the docs did
**not** cover — see `spec.md` Decision 2 and `plan.md` §2: `event` lowercase on the real
account (docs show it capitalized — mitigated with `LOWER(event)`, never a fixed
casing); `NrAiIncident` is 1 row per transition paired by `incidentId`, not by
`conditionId`+`entity.guid`; `operator`/`title` arrive HTML-entity-encoded; `threshold`
is a JSON string.
**No fixture saved yet with the verbatim payload** — the real condition/policy/entity
names expose the user's account's internal topology in a public OSS repo. T-01c creates
a synthetic fixture before T-10's gate.

### [x] T-02 · Verify the uncertain points from `plan.md` §6
Done in the 2026-08-11 review — results in `plan.md` §6. Points 2, 3, and 5 resolved; 5
plan errors fixed.
**Remaining:** see T-02b.

### [x] T-01c · Create a synthetic fixture from the real payload
**Depends on:** T-01b. The real payload (T-01b) stays **only** in this conversation — it
never becomes a file in the repo. Recreate the same shape (fields, types, HTML-entity
encoding in `operator`/`title`, an open+close pair sharing an `incidentId`, `threshold`
as a string) with **generic/fictitious** `conditionName`/`policyName`/`entity.name` and
`runbookUrl` (e.g. `checkout-latency`, `checkout-service`,
`https://runbooks.example.com/...`) — no real name from the user's account.
**Gate:** `tests/fixtures/new_relic/incidents_response.json` (or inlined per `plan.md`
§4) contains no token that appears in the real payload captured in T-01b; T-10's test
passes against this fixture.
**Done 2026-08-11.** Fixture created with fictitious names (`checkout-latency`,
`checkout-service`, `payments-worker`) reproducing every Decision-2 quirk: an
open+close pair sharing one `incidentId` (lowercase `event`), a second pair using the
docs' capitalized `Open`/`Close`, a third still-open incident on the same
condition/entity (3 distinct `incidentId`s = flapping), HTML-entity-encoded
`operator`/`title`, `threshold` as a JSON string, and `runbookUrl` populated on some
rows / `null` on another. **Gate verified:** grepped for `arcotech`/`sre@arco`/the
real account id — no matches; `python3 -m json.tool` confirms valid JSON;
`tests/tools/test_new_relic_alerts_tool.py` parses it successfully (17 passing cases).

### [x] T-02b · Confirm which `slash_catalog.py` to edit
**Done 2026-08-11.** Read both in full. `surfaces/interactive_shell/command_registry/slash_catalog.py`
does **not** duplicate the `/template` help text — it has no vendor list of its own. It
imports `MCP_BY_COMMAND` from `tools/interactive_shell/shared/slash_catalog.py` and
*derives* `SlashCommandSpec`s from the live `SLASH_COMMANDS` registry at call time
(`_resolve_mcp_fields`, `build_slash_command_specs`); its only static string mentioning a
vendor name is an unrelated `/integrations` positional-arg example ("datadog"), not the
`/template` list. **Only** `tools/interactive_shell/shared/slash_catalog.py:376-379` (the
`/template` entry) needed the New Relic addition — edited in T-14.
**Gate verified:** confirmed by reading both files; the template list is not duplicated.

---

## Phase 1 — Integration

### [x] T-03 · `config/constants/new_relic.py` + re-export
Done 2026-08-11. Added the 4 env names plus the two vendor limits and two OpenSRE
defaults, with `__all__`; re-exported from `config/constants/__init__.py`.
**Gate verified:** `uv run python -c "from config.constants import NEW_RELIC_API_KEY_ENV"`
prints `NEW_RELIC_API_KEY` with no import error.

### [x] T-04 · `integrations/new_relic/config.py` and `__init__.py`
Done 2026-08-11. `NewRelicIntegrationConfig` (api_key/account_id/base_url/integration_id)
+ `classify()`, mirroring Datadog's both-fields-required pattern. FR-1, FR-2.
**Gate verified:** `uv run python -c` checks — `base_url=""` normalizes to
`https://api.newrelic.com`; `classify({"api_key": ...}, ...)` (no `account_id`) returns
`(None, None)`; `classify` with both fields returns `("new_relic")`. Also pinned in
`tests/integrations/new_relic/test_client.py` fixtures (config used throughout).

### [x] T-05 · `integrations/new_relic/client.py`
Done 2026-08-11. `NewRelicClient` with `is_configured`, `probe_access()`, `run_nrql()`
(the only method that speaks GraphQL for data queries), `query_incidents()`, and
`query_metrics()`. Mandatory checking order implemented in `_request_graphql`: HTTP
status class → `errors` → `data`. NFR-1 (no raise, no stack trace), NFR-2 (API key never
interpolated into any message), NFR-7 (`httpx.TimeoutException` and a
timeout-worded GraphQL error both map to a distinct `error_type="timeout"`, never
folded into an empty/successful result).
**Gate verified:** `uv run python -m pytest tests/integrations/new_relic/test_client.py -v`
— 12 passed, covering 200+errors, 401 vs. unreachable-account, 429/5xx, a realistic
(synthetic) `NrAiIncident` fixture, empty ≠ error, and timeout ≠ empty.

### [x] T-06 · `verifier.py` + `setup.py`
Done 2026-08-11. `verify_new_relic = register_probe_verifier("new_relic", ...)`;
`NEW_RELIC_SETUP = IntegrationSetupSpec(...)` with 3 fields (`api_key` `secret=True`).
FR-4, FR-5.
**Gate verified:** `tests/integrations/new_relic/test_client.py` pins
`probe_access()` returning distinct `ProbeResult.failed(...)` details for an invalid key
(HTTP 401) vs. a valid key with an unreachable account (`actor.account` is `null`), and
distinct `error_type`s (`rate_limited`/`server_error`) for 429/5xx — none of the three
messages contain vendor stack-trace detail. `opensre integrations verify new_relic`
itself is not yet reachable end-to-end: that requires the registry/CLI wiring in
Phase 2 (T-07), which is out of this phase's scope.

---

## Phase 2 — Integration wiring

### [x] T-07 · `registry.py`, `_catalog_impl.py`, `catalog.py`, `effective_models.py`, `cli.py`
Done 2026-08-11. `IntegrationSpec(service="new_relic", ...)` added with free
`setup_order=53`/`verify_order=59` slots; `_catalog_impl.py` env-loader block
(dual `api_key`+`account_id` gate, mirroring Datadog); `catalog.py` uses
`_all_env("NEW_RELIC_API_KEY", "NEW_RELIC_ACCOUNT_ID")` (not `_any_env`, FR-2);
`EffectiveIntegrations.new_relic` field added; `_setup_new_relic()` registered
in `cli.py`'s `_HANDLERS`.
**Gate verified:** `tests/integrations/test_registry.py`,
`test_registry_invariants.py`, `test_verify.py`, and
`test_verification_registry.py` (89 tests) all green — this also fixes the
Phase-1-known `test_list_verifiers_matches_supported_services` regression.
`NEW_RELIC_API_KEY=... NEW_RELIC_ACCOUNT_ID=... opensre health` lists
`new_relic  local env  FAILED  New Relic API returned HTTP 401.` (fake key,
live 401 as expected — proves env discovery + verify wiring, acceptance
criterion 1).

### [x] T-08 · `.env.example`
Done 2026-08-11. `# New Relic` block added (after Honeycomb) with
`NEW_RELIC_API_KEY=`, `NEW_RELIC_ACCOUNT_ID=`, and a commented-out
`# NEW_RELIC_INSTANCES=` line.
**Gate verified:** `git status --short` shows no modified `.env`, only
`.env.example`.

---

## Phase 3 — Tools

### [x] T-09 · `query_new_relic_metrics`
`new_relic_metrics_tool/` package with `tool.py` + `validation.py`. FR-7, NFR-3, NFR-4,
NFR-8. Inject default `SINCE`/`LIMIT` when the model's NRQL doesn't include them — with
the API's 5 s timeout, a query with no window is a guaranteed failure on a large
account.
**Gate:** `BaseToolContract` green; default-injection test; a test that the `nrqlQuery`
returned by T-10 is accepted as valid input (FR-7); `input_schema` with no
`["type", "null"]`.
**Done 2026-08-11.** `NewRelicMetricsTool` (class-based `BaseTool`); `validation.py`
rejects empty/non-`SELECT`/mutation-shaped input, injects a default `SINCE`/`LIMIT`
clause when the model's NRQL omits them, and clamps an oversized explicit `LIMIT` down
to `NEW_RELIC_NRQL_LIMIT_MAX`. **Gate verified:**
`tests/tools/test_new_relic_metrics_tool.py` — 20 passing cases, including
`test_nrql_query_from_alerts_tool_round_trips_into_metrics_tool` (feeds the alerts
tool's parsed `nrql_query` straight into `query_new_relic_metrics`) and the
default-injection/clamp cases; `input_schema` has no `["type", "null"]` union.

### [x] T-10 · `query_new_relic_alerts`
**Depends on:** T-01c (synthetic fixture). `new_relic_alerts_tool/` package with
`tool.py` + `results.py`. FR-6, NFR-4, NFR-8.
**Gate:** `event` compared without a fixed casing (`LOWER(event)`, tested with both
`'open'` and `'Open'` — the two sources seen disagree); pairing by `incidentId` (the
`open` row + the `close` row of the same transition collapse into one record with a
derived `status`); different `incidentId`s of the same condition **don't** collapse
(test proves 3 flapping firings show up as 3 records); `operator`/`title` decoded from
HTML entities (`&gt;=` → `>=`); `threshold` parsed from a string; `muted` flagged and
**not** filtered; causal order preserved; truncation flagged; the parser runs against
the T-01c fixture.
**Done 2026-08-11.** `NewRelicAlertsTool` (class-based `BaseTool`) + `results.py`
(`parse_incident_rows`), pairing raw rows by `incidentId` with `.lower()` comparison on
`event`. **Gate verified:** `tests/tools/test_new_relic_alerts_tool.py` — 17 passing
cases pin every quirk against the T-01c fixture: lowercase + capitalized `event` both
pair correctly, 3 distinct `incidentId`s on `checkout-latency`/`checkout-service` stay 3
records (flapping, not collapsed), `operator`/`title` HTML-entities decoded, `threshold`
string `"3.0"` parsed to `3.0` with the raw string preserved, `muted: true` flagged (not
filtered), causal (`openTime` ascending) order preserved, and `truncated` flips `True`
when the raw row count hits the requested cap.

### [x] T-11 · `tools/registry_discovery.py` + telemetry
**Gate:** `tests/tools/test_registry.py` proves the real registry discovers both tools
(acceptance criterion 3). `tests/tools/test_telemetry.py` updated.
**Done 2026-08-11.** Added `"integrations.new_relic.tools"` to
`INTEGRATION_TOOL_PACKAGES` (alphabetical, between `mysql` and `openclaw`); added
`query_new_relic_alerts`/`query_new_relic_metrics` to
`test_telemetry.py::_TOOLS_WITHOUT_DELIBERATE_CATCH` (both let unexpected exceptions
escape to the global `BaseTool.__call__` wrapper — no deliberate catch-and-report
pattern to migrate). **Gate verified:**
`tools.registry.get_registered_tool_map()` returns both tool names with
`source="new_relic"` against the real discovery path (not a mock);
`test_every_registered_tool_is_migrated_or_allowlisted` and
`test_every_migrated_tool_has_a_parameterised_failure_case` pass; full
`tests/tools/test_registry.py` (36 tests) and `tests/tools/test_telemetry.py` (34
tests) green.

---

## Phase 4 — Investigation

### [x] T-12 · Routing and intake
`alert_source_catalog.py` (routing + aliases) and `intake/node.py`. FR-8.
No regex/keyword routing outside the declarative tables.
**Gate:** an alert mentioning "newrelic.com" classifies as `alert_source: "new_relic"`.
**Done 2026-08-11.** `_ROUTING_TABLE["new_relic"] = routing(("new_relic",), ("new_relic",))`
and `_ALIASES_TABLE["new_relic"] = ("new relic", "newrelic", "nrql", "nr alert")` added to
`alert_source_catalog.py`; a `"new_relic" for New Relic or newrelic.com` detection line
added to the `_EXTRACT_PROMPT` vendor list in `intake/node.py`, matching the format of the
existing Datadog/Honeycomb/SigNoz lines exactly. No keyword/regex matching added outside
these two declarative tables. **Gate verified:**
`tests/core/domain/alerts/test_alert_source.py::test_primary_sources_for_alert_new_relic`
(routing lookup) and
`test_relevant_sources_matches_newrelic_url_in_alert_text` (an alert message containing
`https://one.newrelic.com/...` resolves `relevant_sources_for_alert(..., ["new_relic", ...])
== ["new_relic"]` via the registered alias, the same mechanism/test style used for `eks` in
the same file); confirmed live via
`seed_tool_sources_for_alert({"alert_source": "new_relic"}) == ("new_relic",)` against the
real registered catalog (not a mock). Full `tests/core/domain/alerts/test_alert_source.py`
(27 tests) green.

### [x] T-13 · Reporting
`provenance.py` and `evidence_catalog.py`. FR-10.
**Gate:** the RCA report cites New Relic in the provenance and the evidence catalog.
**Done 2026-08-11.** `provenance.py`: `PROVENANCE_SOURCE_ALIASES["new_relic_alerts"] =
"new_relic"` plus a provenance block (label "New Relic", `account=<account_id>,
window=<since_minutes>m`, mirroring the Datadog/Honeycomb `if <vendor>:` block shape).
`evidence_catalog.py`: `SOURCE_ALIASES["new_relic"] = "new_relic_alerts"`, a new
`_add_new_relic_alerts()` helper mirroring `_add_datadog_monitors()` (open-incident count in
the label, incident count in the summary, top 3 `condition_name`s as the snippet), called
from `build_evidence_catalog()` alongside the other vendor `_add_*` calls.
**Known limitation, not New Relic-specific:** traced the real evidence-merge path
(`tools/investigation/stages/gather_evidence/tools.py::merge_tool_evidence`) — only the
Grafana tools have a flattening branch there; Datadog monitors/events, Honeycomb traces,
Coralogix, and Betterstack have the identical gap (their `evidence_catalog.py` readers are
only exercised by hand-built test state, never by the live ReAct loop, which only writes
`evidence[tool_name]` = raw output for non-Grafana tools). New Relic's `_add_new_relic_alerts`
follows the exact same pre-existing pattern as those vendors — not a new gap, and out of this
task's declared file scope to fix project-wide. **Gate verified:**
`tests/delivery/test_report_provenance.py::test_build_report_context_cites_new_relic_in_provenance_and_evidence_catalog`
(new, mirrors `test_build_report_context_adds_additional_source_provenance`) — asserts both
`ctx["source_provenance"]["new_relic"]["summary"]` and
`ctx["evidence_catalog"]["evidence/new_relic/alerts"]["provenance"]`/`label`. Full
`tests/delivery/test_report_provenance.py` (16 tests) green.

### [x] T-14 · Alert template
`alert_templates.py`, `config/constants/investigation.py`, `slash_catalog.py` (file
confirmed by T-02b). FR-12.
**Gate:** `opensre investigate --template new_relic` runs and invokes at least one of
the tools (acceptance criterion 4).
**Done 2026-08-11.** Added a `new_relic` entry to `_ALERT_TEMPLATES` in
`alert_templates.py` (`alert_source: "new_relic"`, fictitious `checkout-latency`/
`checkout-service` names, mirroring the Honeycomb/Coralogix template shape);
`"new_relic"` appended to `ALERT_TEMPLATE_CHOICES` in `config/constants/investigation.py`;
`/template` help text in `tools/interactive_shell/shared/slash_catalog.py` updated to list
`new_relic`. **Gate verified:** the real CLI flag is `--print-template` (`--template` in
the task/plan wording is shorthand) —
`uv run opensre investigate --print-template new_relic` prints the correct JSON payload
live (no mock); new smoke test
`tests/cli/test_smoke.py::test_investigate_print_template_new_relic_smoke` pins this,
mirroring the existing `generic`-template smoke test (the only `--print-template`
end-to-end pattern in the suite — no vendor has a heavier "mocked full investigation"
template test to follow). Tool invocation (acceptance criterion 4's other half) verified
without live credentials by confirming the T-12 routing wiring end to end against the real
catalog: `seed_tool_sources_for_alert({"alert_source": "new_relic"}) == ("new_relic",)`,
and both `query_new_relic_alerts`/`query_new_relic_metrics` declare `source="new_relic"`
(Phase 3), so a real investigation from this template auto-seeds at least one New Relic
tool call once the integration is configured — the full live run needs a configured
account and is deferred to the PR demo gate (T-19), per the New Relic key's read-only
operational restriction. `tests/cli/test_args.py`'s `ALERT_TEMPLATE_CHOICES[0]`-based
parametrization already covers the new template generically, no per-template edit needed
there.

---

## Phase 5 — Onboarding and UI

### [x] T-15 · Wizard + UI labels
`plan.md` §3.5 table — watch out for the **two** maps in
`_integration_configurators.py`.
**Gate:** `opensre onboard` shows New Relic in the Observability group and completes
setup; `tests/cli/wizard/` covers the choice.
**Done 2026-08-11.** All 7 files in the §3.5 table wired: `_configure_new_relic()`
in `observability.py`; both the dispatch map (~L93) and the service-label map
(~L128) in `_integration_configurators.py` (confirmed both existed, mirroring
Honeycomb/Coralogix); `Choice(value="new_relic", ...)` in `onboard_integrations.py`;
a sample-alert entry in `constants.py`; `banner_state.py` (replaced a pre-existing
but dead `"newrelic"` key — never produced by the catalog — with the correct
`"new_relic"` slug); `investigation_outcome.py` (used keyword `"new relic"`, not
`"new_relic"`, since real client error messages contain the vendor name with a
space, not an underscore); and 3 template-entry spots in
`command_registry/investigation.py`. **Gate verified:**
`tests/cli/wizard/test_flow.py::test_run_wizard_configures_new_relic` (new,
mirrors the Honeycomb/Coralogix pattern) plus the full
`tests/cli/wizard/`, `tests/cli/test_investigation_payload.py`, and
`tests/interactive_shell/test_command_registry.py` suites (261 tests) green;
`uv run mypy` on all 7 touched non-test files clean; `make lint` and
`make format-check` clean on the touched files; full `make typecheck` (1729
files) green.

---

## Phase 6 — Docs, tests, and gates

### [x] T-16 · `docs/new_relic.mdx` + `docs.json` + `README.md`
**Done 2026-08-11.** `docs/new_relic.mdx` written following the Datadog/Honeycomb
structure (Prerequisites/Setup/Creating a key/Finding the account ID/Verify/
Troubleshooting/Security), calling out the User-key-vs-License-key mistake and the
"alerts need an existing alert condition" gotcha. `docs/docs.json`: `"new_relic"` added
to the "Observability and incidents" group, alphabetically between `incident_io` and
`openobserve`, **without** the `.mdx` suffix. `README.md`: New Relic moved from the
roadmap-link column into the Observability integrations list (the "wanted" issue #139
link removed from that row). Vale vocabulary
(`docs/styles/config/vocabularies/Mintlify/accept.txt`): added `NerdGraph` and `NRQL`.
**Gate verified:** `docs/docs.json` entry has no `.mdx` suffix (grepped); README no
longer lists New Relic under the roadmap column; docs-only diff needs no code checks
per `CI.md` §0, but `make lint`/`make format-check`/`make typecheck` were run anyway as
part of T-18 and stayed green.

### [x] T-16b · Per-service parametrized test tables
**Done 2026-08-11.** New Relic added to all four curated lists with non-default,
fictitious values (EU-style `base_url=https://api.eu.newrelic.com`, fictitious
`account_id="9876543"`, fictitious `api_key="NRAK-test-fake-0000000000000000000"`):
`test_setup_spec_env_round_trip.py` (`_SUBMITTED` + `_SPECS` + import),
`test_cli_spec_setup.py` (`_ANSWERS` + `_CASES` + import),
`test_env_multi_instance.py` (new `NEW_RELIC_INSTANCES` case + `_clear_env`),
`test_catalog_silent_fallback_elimination.py` (`_CLASSIFY_PATCH_TARGETS` **and**
`_ENV_LOADER_CASES`).
**Gate verified:** all four files green —
`uv run python -m pytest tests/integrations/test_setup_spec_env_round_trip.py
tests/integrations/test_cli_spec_setup.py tests/integrations/test_env_multi_instance.py
tests/integrations/test_catalog_silent_fallback_elimination.py -q` → 308 passed, 4
skipped (skips are pre-existing/unrelated to New Relic).

### [x] T-17 · Synthetic / e2e coverage
**Done 2026-08-11.** No single cross-vendor parametrized list exists for this gate
(confirmed by research: Honeycomb/Coralogix/groundcover have no synthetic/e2e entry at
all; SigNoz/Tempo/Temporal/Jenkins/Dagster each get a bespoke
`tests/synthetic/test_<vendor>_scenario.py`). Added
`tests/synthetic/test_new_relic_scenario.py` following that same file-per-vendor shape,
plus one step further: instead of only calling the tool function directly, its main
scenario test drives the **real** `ConnectedInvestigationAgent().run(state)` ReAct loop
against the **real** tool registry (`get_available_tools` is left unpatched) with a
scripted LLM and a mocked `NewRelicClient` — proving `query_new_relic_alerts` is
discovered via the real `is_available`/registry path and invoked by the planner, not
just present in a manual registry listing.
**Gate verified:** `uv run python -m pytest tests/synthetic/test_new_relic_scenario.py
-q` → 3 passed (`test_new_relic_alert_source_maps_to_tools`,
`test_new_relic_tools_are_registered` — real `get_registered_tools()` — and
`test_planner_discovers_and_invokes_new_relic_alerts_through_the_real_loop`).

### [x] T-18 · CI harness
```bash
git status --short
make lint
make format-check
make typecheck
make verify-integrations-smoke
make verify-integrations          # integration config/wiring changed
uv run python -m pytest tests/integrations/ tests/tools/ tests/cli/ tests/interactive_shell/ tests/delivery/ tests/core/domain/alerts/ tests/synthetic/ tests/e2e/ -q
uv run mypy tests/integrations/new_relic tests/tools/test_new_relic_alerts_tool.py tests/tools/test_new_relic_metrics_tool.py tests/cli/wizard/test_flow.py
```
**Done 2026-08-11.** Full checklist run — `git status --short` clean plan (only this
phase's files touched); `make lint`, `make format-check`, `make typecheck` all green;
`make verify-integrations-smoke` green (31 passed); `make verify-integrations` green,
including a **live** `new_relic ✓ passed` row (the account already configured in this
dev environment's local env — a read-only NerdGraph identity+account probe, consistent
with the plan's operational restriction; no account details recorded anywhere in the
repo). Full targeted pytest run across all eight directories: `tests/integrations/`,
`tests/tools/`, `tests/cli/`, `tests/interactive_shell/`, `tests/delivery/`,
`tests/core/domain/alerts/`, `tests/synthetic/` all green. `tests/e2e/` has 1 pre-existing
failure + 1 pre-existing error (`upstream_apache_flink_ecs`/`upstream_lambda`
`test_agent_e2e.py`), both requiring live AWS ECS/Lambda infra and a real
`ANTHROPIC_API_KEY` not present in this sandbox — confirmed pre-existing and unrelated to
New Relic (same failure reproduces on `main`-equivalent code via `git stash`; New Relic
touches none of those files/paths). `uv run mypy` on the
requested test paths: New Relic's own test files are clean; `test_flow.py` has 20
pre-existing mypy errors confirmed via `git stash` to predate this phase entirely (CI's
`make typecheck` does not cover `tests/`, per `AGENTS.md`) — not a New Relic regression.
Also checked `.github/ci/test_scope_rules.py`: no dedicated `integrations/new_relic/`
rule exists (falls through to the generic `integrations/` → `tests/integrations/` rule),
which is already included above.
**Gate verified — all green, see the CI harness output pasted in the PR/agent report.**

### [ ] T-19 · PR
Fill out `.github/PULL_REQUEST_TEMPLATE.md` — includes a **mandatory** AI-usage
disclosure section. Attach an end-to-end screenshot/GIF (acceptance criterion 9).
**Gate:** template complete, demo attached, reference to issue #139.

---

## Traceability map

| Requirement | Tasks |
|---|---|
| FR-1, FR-2 | T-04 |
| FR-3, FR-11 | T-07 |
| FR-4, FR-5 | T-06 |
| FR-6 | T-10 |
| FR-7 | T-09 |
| FR-8 | T-12 |
| FR-9 | T-11, T-17 |
| FR-5 (tiers) | T-06, T-16b |
| FR-10 | T-13 |
| FR-12 | T-14 |
| NFR-1, NFR-2 | T-05 |
| NFR-3, NFR-4 | T-09, T-10 |
| NFR-7 (timeout ≠ empty) | T-05 |
| NFR-8 (context-sized caps) | T-09, T-10 |
| NFR-5 | T-03, T-07 |
| NFR-6 | T-18 (import-linter in `make lint`) |
