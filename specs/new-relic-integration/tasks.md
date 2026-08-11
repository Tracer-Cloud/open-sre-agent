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

### [ ] T-01c · Create a synthetic fixture from the real payload
**Depends on:** T-01b. The real payload (T-01b) stays **only** in this conversation — it
never becomes a file in the repo. Recreate the same shape (fields, types, HTML-entity
encoding in `operator`/`title`, an open+close pair sharing an `incidentId`, `threshold`
as a string) with **generic/fictitious** `conditionName`/`policyName`/`entity.name` and
`runbookUrl` (e.g. `checkout-latency`, `checkout-service`,
`https://runbooks.example.com/...`) — no real name from the user's account.
**Gate:** `tests/fixtures/new_relic/incidents_response.json` (or inlined per `plan.md`
§4) contains no token that appears in the real payload captured in T-01b; T-10's test
passes against this fixture.

### [ ] T-02b · Confirm which `slash_catalog.py` to edit
Both exist: `tools/interactive_shell/shared/slash_catalog.py:377` (has the `/template`
help) and `surfaces/interactive_shell/command_registry/slash_catalog.py` (not
inspected).
**Gate:** know whether the template list is duplicated in both; §3.4 points at the
right file(s).

---

## Phase 1 — Integration

### [ ] T-03 · `config/constants/new_relic.py` + re-export
The 4 env names + `__all__`; re-export in `config/constants/__init__.py`.
**Gate:** `uv run python -c "from config.constants import NEW_RELIC_API_KEY_ENV"`.

### [ ] T-04 · `integrations/new_relic/config.py` and `__init__.py`
`NewRelicIntegrationConfig` + `classify()`. FR-1, FR-2.
**Gate:** normalization test — empty `base_url` → US default; `api_key` without
`account_id` → `classify` returns `(None, None)`.

### [ ] T-05 · `integrations/new_relic/client.py`
Plan §2. **No longer depends on T-01b** — the shape is confirmed by the docs; T-01b's
real fixture hardens the test later, without blocking the code.
Mandatory checking order: HTTP → `errors` → `data`. NFR-1, NFR-2, NFR-7 (5 s timeout as
its own `error_type`, distinct from an empty result).
**Gate:** `tests/integrations/new_relic/test_client.py` with the `plan.md` §4 cases
passing, including `200 + errors` and **timeout ≠ empty**.

### [ ] T-06 · `verifier.py` + `setup.py`
`register_probe_verifier`; `IntegrationSetupSpec` with 3 fields (`api_key`
`secret=True`). FR-4, FR-5.
**Gate:** `opensre integrations verify new_relic` distinguishes an invalid key, an
unreachable account, and a 429 — three messages, none with a stack trace.

---

## Phase 2 — Integration wiring

### [ ] T-07 · `registry.py`, `_catalog_impl.py`, `catalog.py`, `effective_models.py`, `cli.py`
**Depends on:** T-06. `plan.md` §3.3 table.
FR-3 (`resolve_env_credential`, never `os.getenv` for the key), FR-11, NFR-5.
Watch out: `catalog.py` uses `_all_env` (not `_any_env`), and `setup_order`/
`verify_order` must be **unique** with a matching entry in `_HANDLERS` — the 4
invariants in `plan.md` §3.3 are CI gates.
**Gate:** with `NEW_RELIC_API_KEY` + `NEW_RELIC_ACCOUNT_ID` in the environment,
`opensre integrations show` lists `new_relic` (acceptance criterion 1).
`uv run python -m pytest tests/integrations/test_registry.py tests/integrations/test_registry_invariants.py tests/integrations/test_verify.py`

### [ ] T-08 · `.env.example`
`# New Relic` block + commented `NEW_RELIC_INSTANCES=`.
**Gate:** `git status --short` shows no modified `.env`.

---

## Phase 3 — Tools

### [ ] T-09 · `query_new_relic_metrics`
`new_relic_metrics_tool/` package with `tool.py` + `validation.py`. FR-7, NFR-3, NFR-4,
NFR-8. Inject default `SINCE`/`LIMIT` when the model's NRQL doesn't include them — with
the API's 5 s timeout, a query with no window is a guaranteed failure on a large
account.
**Gate:** `BaseToolContract` green; default-injection test; a test that the `nrqlQuery`
returned by T-10 is accepted as valid input (FR-7); `input_schema` with no
`["type", "null"]`.

### [ ] T-10 · `query_new_relic_alerts`
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

### [ ] T-11 · `tools/registry_discovery.py` + telemetry
**Gate:** `tests/tools/test_registry.py` proves the real registry discovers both tools
(acceptance criterion 3). `tests/tools/test_telemetry.py` updated.

---

## Phase 4 — Investigation

### [ ] T-12 · Routing and intake
`alert_source_catalog.py` (routing + aliases) and `intake/node.py`. FR-8.
No regex/keyword routing outside the declarative tables.
**Gate:** an alert mentioning "newrelic.com" classifies as `alert_source: "new_relic"`.

### [ ] T-13 · Reporting
`provenance.py` and `evidence_catalog.py`. FR-10.
**Gate:** the RCA report cites New Relic in the provenance and the evidence catalog.

### [ ] T-14 · Alert template
`alert_templates.py`, `config/constants/investigation.py`, `slash_catalog.py` (file
confirmed by T-02b). FR-12.
**Gate:** `opensre investigate --template new_relic` runs and invokes at least one of
the tools (acceptance criterion 4).

---

## Phase 5 — Onboarding and UI

### [ ] T-15 · Wizard + UI labels
`plan.md` §3.5 table — watch out for the **two** maps in
`_integration_configurators.py`.
**Gate:** `opensre onboard` shows New Relic in the Observability group and completes
setup; `tests/cli/wizard/` covers the choice.

---

## Phase 6 — Docs, tests, and gates

### [ ] T-16 · `docs/new_relic.mdx` + `docs.json` + `README.md`
**Gate:** entry in `docs/docs.json` **without** the `.mdx` suffix (acceptance criterion
8); New Relic moved from "wanted" to the supported list in `README.md:256`.

### [ ] T-16b · Per-service parametrized test tables
Four curated lists that **don't** fail CI if omitted, but leave a silent gap — table in
`plan.md` §4: `test_setup_spec_env_round_trip.py::_SUBMITTED`,
`test_cli_spec_setup.py` (`_ANSWERS` + `_CASES` + import), `test_env_multi_instance.py`,
`test_catalog_silent_fallback_elimination.py` (two places).
**Gate:** New Relic parametrized in all four, with non-default values.

### [ ] T-17 · Synthetic / e2e coverage
**Gate:** a test proving the planner discovers and invokes the tools through the normal
path (mandatory gate for a new integration).

### [ ] T-18 · CI harness
```bash
git status --short
make lint
make format-check
make typecheck
make verify-integrations-smoke
make verify-integrations          # integration config/wiring changed
uv run python -m pytest <targets from .github/ci/test_scope_rules.py>
uv run mypy tests/integrations/new_relic tests/tools   # CI doesn't cover tests/
```
**Gate:** all green. List the focused tests run in the PR description.

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
