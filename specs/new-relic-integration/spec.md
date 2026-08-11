# Spec: New Relic integration (alerts + metrics)

- **Status:** draft — awaiting review
- **Feature ID:** `new-relic-integration`
- **Upstream issue:** [#139](https://github.com/Tracer-Cloud/opensre/issues/139) (New Relic listed as "wanted" in `README.md:256`)
- **Date:** 2026-08-11

## 1. Problem

OpenSRE investigates incidents by reading alerts, metrics, logs, and traces from
observability backends. Today it supports Grafana, Datadog, Honeycomb, Coralogix,
groundcover, CloudWatch, Sentry, SigNoz, and others — but **not** New Relic, which is the
primary backend for a relevant slice of users. For those teams, OpenSRE can neither
identify the alert's origin nor collect metric evidence, which degrades RCA to "guessing
without data."

## 2. Objective

A user with a New Relic account should be able to:

1. Configure New Relic through onboarding (`opensre onboard`) or via
   `opensre integrations setup new_relic`, and see the credential get verified.
2. Trigger an investigation from a New Relic alert and have the source automatically
   recognized as `new_relic`.
3. Have the agent collect, without manual intervention, **alerts** (open and recent
   incidents, with the condition/policy that fired) and **metrics** (via NRQL) as
   evidence in the RCA.

## 3. Out of scope

| Item | Reason |
|---|---|
| Logs tool (`Log` via NRQL) | Not requested. Recorded in §8 as a low-cost extension once the client exists. |
| Traces / distributed tracing tools | Same reason. Honeycomb and Tempo already cover traces. |
| Writing to New Relic (ack an incident, create a deployment marker, mute a policy) | This feature is read-only. Every tool declares `SideEffectLevel.READ_ONLY`. **Operational reinforcement:** the real key used to validate this plan (Phase 0) can only run `query`, never `mutation` — any write action, even a test one, requires explicit, clear manual approval from the user before it runs. |
| Entity/topology lookup (`entitySearch`) | Useful but orthogonal; doesn't block alerts + metrics. |
| New Relic as a notification *destination* (watchdog alerts) | The watchdog is a separate feature (`tools/system/watch_dog/`). |

## 4. Decisions

Each decision below was open and was closed with a defensible default. **The plan review
must specifically challenge these four.**

### [DECISION 1] API: NerdGraph (GraphQL), not REST v2

`https://api.newrelic.com/graphql`, header `API-Key: <User key NRAK-…>`.

*Why:* a single API and a single transport cover both alerts and metrics. Alerts REST v2
is on a deprecation path and would require a second client. NerdGraph is the supported,
versioned surface.

*Accepted consequence:* GraphQL returns **HTTP 200 with a populated `errors`** field on
query/partial-authorization failures. The client must treat this as a structured error —
it is failure mode #1 for this integration and has a dedicated test.

*Verified against official docs (2026-08-11):* US endpoint
`https://api.newrelic.com/graphql` and EU `https://api.eu.newrelic.com/graphql`; header
`API-Key` with a **User key** (not a License key). Operational limits confirmed, all with
design impact:

| Limit | Value | Consequence |
|---|---|---|
| **NRQL timeout via API** | **5 s** (default; only via API, not in the UI) | Hard constraint. Wide windows or unaggregated queries will time out. Tools keep a short default window, and the client treats timeout as a structured error, not "no data." |
| Max `LIMIT` | 5,000 | The NFR-4 cap sits **well** below this (order of 100–200 records) for LLM context reasons, not because of the API limit. |
| Concurrency | 25 simultaneous requests per user | A single investigation doesn't come close; a fleet of parallel investigations might. |
| Rate | 3,000 queries per account per minute | No realistic risk in the loop. |

### [DECISION 2] Alerts via NRQL over `NrAiIncident`, not the `aiIssues` GraphQL query

The alerts tool uses `run_nrql()` over the `NrAiIncident` event type, not the
`actor.account.aiIssues.issues` query.

*Why:* shares exactly the same transport, parser, and error handling as the metrics tool
(DRY — one tested `run_nrql` serves both). `NrAiIncident` carries what RCA needs:
`conditionName`, `policyName`, `priority`, `openTime`, `closeTime`, entity name. Using
`aiIssues` would mean a second, nested GraphQL parsing path with its own cursor
pagination — more surface for the same diagnostic data.

*✅ VERIFIED against official docs (2026-08-11).* All assumed attributes exist, with the
exact spelling: `conditionName`, `policyName`, `priority`, `openTime`, `closeTime`,
`entity.name`, `incidentId`, `conditionId`, `policyId`, `event`.

**Factual correction (docs):** `event` has values **`Open`/`Close` capitalized**, not
`open`/`close` as originally assumed. NRQL compares strings case-sensitively, so
`WHERE event = 'open'` returns zero rows — **silently**, with no error.

**✅✅ VALIDATED against a real account (2026-08-11) — and the docs were incomplete.** I
ran the `plan.md` §2 NRQL against a real production account (via the user's key,
read-only use, see the operational restriction in §3). Two findings the docs didn't make
clear:

1. **`event` came back lowercase (`"open"`/`"close"`) on this account**, contradicting
   the documented capitalization. Conclusion: casing **is not guaranteed by either source
   alone** — the docs show a capitalized example, the real account returns lowercase.
   **Design decision:** never compare `event` with a fixed casing. Use
   `LOWER(event) = 'open'` in the NRQL itself (or `.lower()` on the Python side if the
   filter is client-side). This closes the risk for good, instead of betting on one
   spelling.
2. **`NrAiIncident` emits ONE ROW PER STATE TRANSITION, not one row per incident.** A
   resolved incident shows up as **two rows sharing the same `incidentId`** — one with
   `event="open"` (and `closeTime=null`) and another with `event="close"` (and
   `closeTime` populated). An incident still open only has the `open` row. This changes
   the dedup strategy from Decision 2: **the pairing key is `incidentId`**, not
   `conditionId` + `entity.guid`. Repeated firings of the same condition **have distinct
   `incidentId`s** and are a real flapping signal (e.g. one condition opened and closed 3
   times in 30 minutes on the test account) — collapsing by condition+entity would
   destroy that signal instead of cleaning up noise.

**Two additional shape discoveries, only visible with real data (not in the docs):**

| Field | Real behavior | Consequence for the parser |
|---|---|---|
| `operator`, `title` | Come back **HTML-entity-encoded** (`"&gt;="` instead of `">="`) | `results.py` needs `html.unescape()` before exposing it to the agent — otherwise the LLM reads a literal `&gt;=`. |
| `threshold` | JSON **string** (`"3.0"`), not a number | Don't assume a numeric type; parse explicitly if used in a comparison. |
| `runbookUrl` | `null` on APM-based conditions (`Web Transaction Errors`), populated on custom workload-based conditions | Confirms it's optional — already marked nullable, real behavior matches. |

**Discovery that increases the feature's value.** The docs list attributes I didn't know
existed that are exactly what an RCA needs:

| Attribute | Why it matters |
|---|---|
| **`nrqlQuery`** | The **exact NRQL query the condition evaluates**. The agent reads the alert and can *re-run the alert's own query* via `query_new_relic_metrics`. It's the direct bridge between the two tools — a real capability, not a convenience. |
| `runbookUrl` | The runbook the team already wrote for this alert. First-order evidence. |
| `threshold`, `operator`, `valueFunction`, `thresholdDuration` | *Why* it fired, not just *that* it fired. |
| `muted`, `mutingRuleId`, `closeCause` | Separates signal from noise: a muted incident is not signal. |
| `title`, `description` | Human context already written. |
| `entity.guid`, `entity.type`, `targetName` | Enables the topology extension in §8 without changing the parser. |

**Known and accepted limitation.** In New Relic, an *issue* aggregates multiple
*incidents* (the docs say "in some cases an issue can have thousands of incidents"; an
issue goes "idle" past 5,000). `NrAiIncident` **does not carry the issue id** —
correlation and grouping only exist via `aiIssues`. Consequence: if an issue aggregates
50 incidents, the agent sees 50 records, not one grouped event. If the loss of grouping
becomes a problem in practice, `aiIssues` comes in as a **complement** (not a
replacement) — it brings `isCorrelated`, `totalIncidents`, `incidentIds`,
`acknowledgedAt`, which `NrAiIncident` doesn't have.

**Aggregation strategy in `results.py` (corrected against the real account):** pair rows
by `incidentId` — each incident becomes **one** logical record with a derived `status`
(`closeTime is None` → `"open"`; otherwise `"closed"`, using the data from the `close`
row, which has the final `title`/context). **Do not** collapse different `incidentId`s
from the same `conditionName`/entity — that's real flapping (confirmed on the test
account: one condition opened and closed 3× in 30 minutes), not noise to hide. `muted`
stays flagged, never filtered.

*Retention risk closed.* T-01b ran against the real account: the query returned 20
incidents within a 30-day window with no error, confirming sufficient retention for RCA
on that account (the exact per-plan retention number, via the Data Retention UI, has not
been read — doesn't block, stays as a docs note in case a user's plan has shorter
retention).

### [DECISION 3] Region via an explicit `base_url`, not a `region` field

`base_url` field defaulting to `https://api.newrelic.com`; EU tenants set
`https://api.eu.newrelic.com`.

*Why:* it's literally the Honeycomb pattern (`integrations/honeycomb/config.py`, whose
docstring says `base_url` "only moves for EU tenants"). Reuses the existing
`normalize_url` validator. A `region: us|eu` field would be one more map to maintain and
one more translation path to test, for the same UX once documented.

### [DECISION 4] One alerts tool, not two

`query_new_relic_alerts` returns incidents **already enriched** with `conditionName`,
`policyName`, `nrqlQuery`, `threshold`/`operator`, and `runbookUrl`, instead of a separate
tool for policy/condition configuration.

*Why:* confirmed during verification — `NrAiIncident` brings **all of this in the same
record**. There's no second call to make. A separate "condition configuration" tool
wouldn't have anything to fetch that isn't already here, and would only increase the
surface the planner has to disambiguate. Verification reinforced this decision rather
than weakening it.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | `NewRelicIntegrationConfig` normalizes `api_key`, `account_id`, `base_url`, `integration_id`. An empty `base_url` falls back to the US default. |
| FR-2 | `classify()` only recognizes the integration as configured when **both `api_key` and `account_id`** are present. Either one alone doesn't configure anything usable. |
| FR-3 | The credential resolves in the order store → env → keyring, via `resolve_env_credential` for `NEW_RELIC_API_KEY`. Plain `os.getenv` for this name is forbidden (rule from `docs/adding-tools-and-integrations.md` §Credential resolution). |
| FR-4 | `opensre integrations verify new_relic` validates the User key **and** access to the given account, distinguishing "invalid key" from "valid key, account unreachable." |
| FR-5 | `opensre integrations setup new_relic` and the onboarding wizard collect the 3 fields. `api_key` uses `secret=True`, which controls **only prompt masking**. The persistence tier is derived from the env var *name* by `config.env_file.is_sensitive_env_key` ("Fields do not get to choose"): `NEW_RELIC_API_KEY` has terminal token `key` → keyring; `NEW_RELIC_ACCOUNT_ID` (terminal `id`) and `NEW_RELIC_API_URL` (terminal `url`) → `.env`. Verified against `config/env_file.py:52-63`. |
| FR-6 | `query_new_relic_alerts` returns incidents from a configurable window, filterable by priority and entity, carrying `conditionName`, `policyName`, `nrqlQuery`, `threshold`/`operator`, `runbookUrl`, and `entity.name` (with `operator`/`title` decoded from HTML entities). `muted` incidents come flagged, never silently mixed into the signal. Rows are paired by `incidentId` (the open+close rows of the same transition collapse into one record with a derived `status`); repeated firings of the same condition (distinct `incidentId`s) are **not** collapsed — that's flapping, real signal. `event` is compared without depending on casing (`LOWER(event)`). |
| FR-7 | `query_new_relic_metrics` accepts an NRQL query and returns normalized results, tagged `EvidenceType.METRICS`. Explicitly accepts the `nrqlQuery` returned by FR-6 as input — re-running the alert's own query is the main bridge between the two tools. |
| FR-8 | An alert whose source mentions New Relic / `newrelic.com` / NRQL is classified as `alert_source: "new_relic"` at intake. |
| FR-9 | With New Relic configured, both tools become visible and invokable in the investigation loop with no manual wiring. |
| FR-10 | New Relic shows up in the RCA report's provenance and evidence catalog. |
| FR-11 | `NEW_RELIC_INSTANCES` supports multiple accounts, like the other integrations. |
| FR-12 | An example alert template exists: `/investigate new_relic` / `opensre investigate --template new_relic`. |

## 6. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | **No vendor exception leaks to an external surface.** 401/403/404/429/5xx and GraphQL `errors` return a structured, investigation-friendly error, never a raise. (CWE-209 / `py/stack-trace-exposure`.) |
| NFR-2 | The User key never appears in logs, in a tool's return value, in observable `extract_params`, or in tool-call kwargs. Goes through `platform/masking/` if it ever ends up composing output. |
| NFR-3 | Each tool's `input_schema` is JSON Schema accepted by **every** LLM provider — no `"type": ["object","null"]` (draft-07 passes local validation and breaks on the first invoke, because all investigation tools go together in the payload). |
| NFR-4 | A large result is deliberately capped and truncation is flagged (`truncated`, `fetch_error`), preserving the incidents' causal order. |
| NFR-5 | No cyclic imports: env var names live in `config/constants/new_relic.py` (a leaf), never inline in the feature module nor in `config/config.py`. |
| NFR-7 | The 5 s NRQL-via-API timeout is treated as a **distinct structured error** ("query timed out — narrow the window"), never as an empty result. Conflating the two would make the agent conclude "no alerts" when the truth is "it didn't have time." |
| NFR-8 | Conservative default windows and `LIMIT`, sized for LLM context (order of 100–200 records), not by the API's 5,000 `LIMIT MAX`. |
| NFR-6 | Respects the boundaries in `docs/ARCHITECTURE.md`: `integrations/new_relic/` does not import from `surfaces/`; `core/` has no knowledge that New Relic exists (routing enters via catalog/registry at runtime). |

## 7. Acceptance criteria

Verifiable, in the order the implementation satisfies them:

1. With `NEW_RELIC_API_KEY` + `NEW_RELIC_ACCOUNT_ID` in the environment,
   `opensre integrations show` lists `new_relic` as configured.
2. `opensre integrations verify new_relic` → success with a valid credential; distinct,
   stack-trace-free messages for an invalid key, an unreachable account, and a 429.
3. Both tools show up in `tools/registry_discovery.py` and are discovered by the real
   registry (automated test, not manual inspection).
4. `opensre investigate --template new_relic` runs an RCA that invokes at least one of
   the tools and cites New Relic in the evidence.
5. A fixture test with a **real** NerdGraph payload (including the `200 + errors` case)
   passes.
6. `make verify-integrations` is green.
7. The `CI.md` checklist is green: lint, format, typecheck, tests.
8. `docs/new_relic.mdx` exists and is registered in `docs/docs.json`.
9. A screenshot/GIF end-to-end demo is attached to the PR (mandatory gate for a new
   integration, `docs/adding-tools-and-integrations.md` §Final gate).

## 8. Future extensions (not in this feature)

- `query_new_relic_logs` — NRQL over `Log`. Low marginal cost: reuses `run_nrql`.
- `entitySearch` to resolve service → entity GUID and enrich topology.
- Deployment markers as temporal-correlation evidence (`NrMarker`/`Deployment`).
- A dedicated correlation provider, following `integrations/datadog/correlation/`.

## 9. Risks

Updated after verification against the official docs (2026-08-11).

| Risk | Severity | State / Mitigation |
|---|---|---|
| ~~`NrAiIncident` shape diverging from what was assumed~~ | ~~High~~ → **Closed** | Confirmed against docs **and** against a real account (2026-08-11). |
| ~~`event` with uncertain casing~~ | ~~High~~ → **Closed** | Docs show it capitalized, the real account returned lowercase — the two sources disagree with each other. Mitigated by design: `LOWER(event)`, never a casing-sensitive comparison. |
| ~~Wrong dedup key (would collapse real flapping)~~ | ~~High~~ → **Closed** | Only discovered with real data: `NrAiIncident` is 1 row per transition, paired by `incidentId`, not `conditionId`+`entity.guid`. Fixed in `results.py` (Decision 2, spec and plan). |
| 5 s NRQL-via-API timeout | **High** | NFR-7 + NFR-8: short default window, timeout as a distinct error from "no data." Not yet tested against a real timeout (the test account responded well within the limit). |
| GraphQL 200 + `errors` treated as success | High | Dedicated test; the client checks `errors` **before** `data`. |
| ~~Insufficient `NrAiIncident` retention on the account~~ | ~~Medium~~ → **Closed** | T-01b confirmed 30 days with no error against a real account. Exact per-plan retention (via the UI) not read — a docs note, not a blocker. |
| Loss of issue → incidents grouping | Medium | Accepted and documented in Decision 2. `aiIssues` as a future complement if it becomes a problem. |
| No real New Relic account for the demo gate | Medium | **T-00.** Blocks acceptance criteria 4, 5, and 9 — not Phase 1 (T-03/T-04/T-06 are independent). |
| 25 requests/user concurrency under a parallel fleet | Low | A single investigation doesn't come close. Recorded for when a fleet exists. |
| Wrong `account_id` giving a silent empty result | Low | FR-4 separates "account unreachable" from "no data." |

## 10. Verification sources

- [Meet NerdGraph: our GraphQL API](https://docs.newrelic.com/docs/apis/nerdgraph/get-started/introduction-new-relic-nerdgraph/) — US/EU endpoints, `API-Key` header
- [New Relic API keys](https://docs.newrelic.com/docs/apis/intro-apis/new-relic-api-keys/) — User key is the type required by NerdGraph
- [Alert event attributes](https://docs.newrelic.com/docs/alerts-applied-intelligence/new-relic-alerts/advanced-alerts/understand-technical-concepts/violation-event-attributes/) — full attribute list for `NrAiIncident`
- [Rate limits with NRQL](https://docs.newrelic.com/docs/nrql/using-nrql/rate-limits-nrql-queries/) and [NerdGraph usage limits](https://docs.newrelic.com/docs/apis/nerdgraph/nerdgraph-usage-limits/) — 5 s timeout, 25 concurrent, 3,000 queries/account/min
- [NRQL result limits increased from 2,000 to 5,000](https://docs.newrelic.com/whats-new/2024/01/whats-new-01-09-nrql-limit-increases/) — `LIMIT MAX`
- [NerdGraph tutorial: Issue and alert event query APIs](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-issues-api-via-github/) — issue↔incidents relationship, `aiIssues` fields, `issueTtl`
