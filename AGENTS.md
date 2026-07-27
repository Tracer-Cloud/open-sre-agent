## OpenSRE Development Reference

## Build and Run commands

- Build `make install` (sets up the project environment via `uv sync` and installs this repo in editable mode)
- Run **`uv run opensre …`** from the repo root while developing — preferred approach, uses this checkout even if another `opensre` is on your `PATH`.
- Use **`uv run python …`** for any Python commands.

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)
- Use named constants for HTTP status codes (`http.HTTPStatus`, e.g.
  `HTTPStatus.PAYMENT_REQUIRED`) in both source and tests — never hardcoded
  numeric literals like `402`.
- Env-var names and shared static constants live under `config/`, never inline
  in a feature module or duplicated across files. Put them in a domain module
  under `config/constants/` (e.g. `config/constants/billing.py`,
  `config/constants/llm.py`) — a leaf that any layer can import without a cycle —
  and re-export via `config/constants/__init__.py`. Do **not** define shared env
  names in `config/config.py`: it imports `config.llm_auth.*`, so a name it and
  one of those modules both need would force a cyclic import. Only a name used
  solely inside `config/config.py` (nothing it imports needs it) may live there.
- Do not keep compatibility-only forwarding modules after refactors. Once imports and tests
  are migrated, remove the old module path in the same change and use one canonical import path.

### Docs under `docs/`

`docs/` is user-facing. Test every sentence: *does this change what the reader
does?* If not, cut it.

- Cut vendor API endpoints (`getMe`), internal function names, which credential
  tier a value lands in, and the bug a change fixed — that belongs in the PR
  description or a module docstring.
- Keep required vs optional, shortcuts that save real work, gotchas that are
  invisible until they bite, and the exact commands and env vars they type.
- Say "a chat the bot was never added to fails during setup", not "`getChat`
  returns `ok: false`".

### Performance (algorithms & data structures)

Apply on the **hot path** (per-request / per-iteration / per-tool-call); leave cold
code simple. Complexity must be deliberate — the simplest structure that meets the
asymptotic need, and no more.

- **Membership / dedup in a loop → `set`/`dict`, never `x in list`.** List `in` is
  O(n); a set is O(1). (frozen dataclasses are hashable, so `set()` works on them.)
- **Resolve once, reuse.** Don't re-scan for something already looked up — if you
  fetched an object from a map, read its fields directly instead of a second linear
  scan. Build an O(1) `{name: obj}` map instead of scanning a list by name.
- **No `deepcopy` / `json.dumps` on the hot path.** If the result is invariant after
  construction, compute it once (`functools.cached_property` / a stored field) and
  treat it read-only. Verify no caller mutates a shared cached object first.
- **Sort the light thing, once.** Sort keys/names (strings), not heavy objects, and
  don't re-sort the same collection twice for two outputs.
- **Right structure for the job:** `collections.deque` for queues/both-ends,
  `heapq` for top-k, `bisect` for sorted search, `OrderedDict.move_to_end` /
  `functools.lru_cache` for bounded caches, `"".join(parts)` never `+=` in a loop.
- **Behavior-preserving refactors are TDD-guarded:** add a characterization test that
  pins the observable behavior, confirm it passes on the *pre*-refactor code, then
  keep it green through the change. Optimize only after; keep any benchmark in the PR.

### File placement (all packages)

When adding or changing behavior, put code in the **owning module first** — not the nearest
shared file that already imports something similar.

| Kind of file | Should contain | Should not contain |
| --- | --- | --- |
| **Orchestration** (`flow.py`, `controller.py`, `lifecycle.py`, `factory.py`) | Stage ordering, wiring, dispatch to specialists | Vendor/provider/domain logic, API clients, heavy UI |
| **Shared UI / prompts** (`_ui.py`, `prompts.py`, generic `validation.py`) | Reusable prompts, tables, rendering, thin dispatch | Logic for one provider, integration, or vendor |
| **Domain / provider / vendor module** (`providers/<name>.py`, `surfaces/cli/wizard/<name>.py`, `integrations/<vendor>/`) | All behavior specific to that provider, vendor, or feature area | Unrelated providers or cross-cutting orchestration |
| **Registry / catalog** (`config.py`, `*_catalog.py`, `provider_registry.py`) | Metadata, defaults, discovery tables | Live API calls, onboarding prompts, retry loops |

**Rules:**

1. **Two or more functions for the same provider, vendor, or feature area** → add or extend
   a dedicated module (or subpackage) for that area. Do not grow a shared orchestration or UI
   file with provider-specific branches.
2. **Before editing a shared file**, check for an existing sibling pattern in the same
   package (`local_llm/`, `providers/azure_openai.py`, `integrations/<vendor>/tools/`, etc.).
   Match that layout before inventing a new one.
3. **Keep dispatch thin.** Shared entrypoints (`validate_provider_credentials`, `get_llm`,
   slash-command handlers) should delegate in a few lines; implementation lives downstream.
4. **Respect package boundaries** in [ARCHITECTURE.md](docs/ARCHITECTURE.md). Surfaces compose
   lower tiers; `core/` and `integrations/` do not import from `surfaces/`.
5. **Package-local detail** lives in that package's `AGENTS.md` when present (e.g.
   [`surfaces/interactive_shell/AGENTS.md`](surfaces/interactive_shell/AGENTS.md),
   [`core/llm/AGENTS.md`](core/llm/AGENTS.md)). Read it before structural changes in that tree.
6. **Tool location** follows [docs/tool-placement-policy.md](docs/tool-placement-policy.md)
   (vendor-specific vs `tools/system/` vs cross-vendor).

If a change would add a new provider-specific `if provider.value == ...` block to a file
that already serves multiple providers, stop and extract a dedicated module instead.

Before any push or PR creation follow [**CI.md**](CI.md) — lint, format, typecheck, and test commands all live there.

When opening a PR, fill out the [**PR template**](.github/PULL_REQUEST_TEMPLATE.md) — it is not optional boilerplate; it has a required AI-usage disclosure section.

## 1. Repo Map

| Path                                          | What it does                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/`                                       | Investigation orchestration, context assembly, the shared runtime tool-calling loop, and domain logic (state, types, correlation rules). Includes `core/tool_framework/` — the `BaseTool` base class, `@tool` decorator, registered-tool primitives, error telemetry, skill-guidance helpers, and shared payload utilities (`utils/`). |
| `surfaces/cli/`                               | Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support. Provider onboarding → `wizard/<provider>.py` (or `wizard/local_llm/`); new subcommands → `commands/<name>.py`. Runtime LLM wiring → [`core/llm/AGENTS.md`](core/llm/AGENTS.md).                                                                                                                                                                                                                                                   |
| `surfaces/interactive_shell/`                 | Interactive terminal (REPL) loop, slash commands, chat/help surfaces, action-planning harness, and terminal UI.                                                                                                                                                                                                                        |
| `integrations/`                               | Per-integration config normalization, verification, clients, helpers, store/catalog logic, the Hermes log pipeline, and per-vendor tool packages under `integrations/<vendor>/tools/`.                                                                                                                                                 |
| `tools/`                                      | Tool registry, per-tool packages for cross-cutting tools that aren't vendor-specific (e.g. `tools/system/fleet_monitoring/`, `tools/system/watch_dog/`, `tools/system/sre_guidance_tool/`), and the interactive-shell action tools. Framework primitives (decorator, base class, utils) live in `core/tool_framework/`.                |
| `platform/`                                   | Cross-cutting platform services: guardrails, masking, sandbox, analytics, auth, notifications, observability, harness ports (`platform/harness_ports.py`), and EC2 deployment (`platform/deployment/`).                                                                                                                                |
| `config/`                                     | Shared constants, prompts, and UI theme.                                                                                                                                                                                                                                                                                               |
| `tests/`                                      | Unit, integration, synthetic, deployment, e2e, chaos engineering, and support tests.                                                                                                                                                                                                                                                   |
| `docs/`                                       | User-facing documentation, integration guides, and docs-site assets.                                                                                                                                                                                                                                                                   |
| `.github/`                                    | CI workflows, issue templates, pull request template, and repository automation.                                                                                                                                                                                                                                                       |
| `Dockerfile`                                  | Optional production container image (FastAPI health app via uvicorn).                                                                                                                                                                                                                                                                  |
| `pyproject.toml`                              | Python project metadata, dependency configuration, tooling, and package settings.                                                                                                                                                                                                                                                      |
| `Makefile`                                    | Canonical local automation for install, test, verify, deploy, and cleanup targets.                                                                                                                                                                                                                                                     |
| `README.md`                                   | Product overview, install, quick start, high-level capabilities, and links to deeper docs.                                                                                                                                                                                                                                             |
| `docs/DEVELOPMENT.md`                         | Contributor workflows: CI parity commands, dev container, benchmark, deployment, telemetry detail.                                                                                                                                                                                                                                     |
| `docs/ARCHITECTURE.md`                        | Package architecture: the four-tier layer table, folder diagram, per-layer responsibilities, allowed cross-layer edges, and cross-layer flows.                                                                                                                                                                                         |
| `docs/investigation-pipeline-architecture.md` | Investigation pipeline stages, ReAct loop control flow, and guardrails (tool cap, stagnation breaker, context budget), with diagrams.                                                                                                                                                                                                  |
| `docs/investigation-tool-calling.md`          | Investigation ReAct tool schemas, LLM invoke payloads, and message shapes (all providers).                                                                                                                                                                                                                                             |
| `docs/tool-placement-policy.md`               | Decision rule for where a tool lives: `integrations/<vendor>/tools/` vs. `tools/system/` vs. `tools/cross_vendor/` vs. `surfaces/shared/`.                                                                                                                                                                                             |
| `docs/NAMING.md`                              | Naming conventions for `core/`: the glossary (State/Snapshot/RunInput/RunResult/Slice/Resources/Budget), the `{domain}_{role}.py` file rule, type naming (`Mixin` suffix, role-named Protocols, no package-name prefix), and anti-patterns.                                                                                            |
| `SETUP.md`                                    | Machine setup (all platforms, Windows, MCP/OpenClaw, troubleshooting).                                                                                                                                                                                                                                                                 |
| `CI.md`                                       | Mandatory pre-push checklist: lint, format, typecheck, tests — agents MUST follow before pushing.                                                                                                                                                                                                                                      |
| `CONTRIBUTING.md`                             | Contribution workflow, branch/PR guidance, and quality expectations.                                                                                                                                                                                                                                                                   |

Main packages one level deeper:

- `platform/analytics/` — Analytics event plumbing and install helpers used by the onboarding flow.
- `platform/auth/` — JWT and authentication helpers for local and hosted runtime access.
- `surfaces/interactive_shell/` — REPL watchdog slash commands (`/watch`, `/watches`, `/unwatch`): PR demo steps live under **Interactive shell: REPL watchdog demo** in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#interactive-shell-repl-watchdog-demo).
- `config/constants/` — Shared prompt and other static constants.
- `platform/deployment/aws/` — Shared boto3 client factory, deployment constants (`config.py`), VPC/subnet/SG helpers, EC2/IAM provisioning, ECR build/push, and SSM run-command primitives. Import from here in deployment scripts instead of duplicating.
- `platform/deployment/` — EC2 deploy/destroy: `opensre-web` and `opensre-gateway` on one instance. Makefile: `make deploy`.
- `platform/guardrails/` — Guardrail rules, evaluation engine, audit helpers, and CLI bindings.
- `platform/harness_ports.py` — Harness port layer (integration resolution, tool registry, investigation tools, GitHub repo scope). Real implementations are wired at startup via `integrations/harness_adapters.py` and `tools/harness_adapters.py` through `install_harness_ports()` in `surfaces/interactive_shell/ui/output/boundary.py`. See `core/agent_harness/AGENTS.md` for the import boundary.
- `integrations/hermes/` — Hermes log tailing, incident classification, correlator, sinks, and investigation bridge.
- `integrations/llm_cli/` — Subprocess-backed LLM CLIs (e.g. Codex). Extension guide: `integrations/llm_cli/AGENTS.md`.
- `platform/masking/` — Masking utilities for redacting or normalizing sensitive content.
- `tools/investigation/` — Composite investigation capability, public entrypoints, semantic stages, and reporting.
- `core/llm/` — Hosted LLM provider clients, retry/schema helpers, and investigation tool-calling adapters.
- `platform/sandbox/` — Sandboxed execution helpers for controlled runtime actions.
- `core/state/` — Shared agent runtime envelope (`AgentState`), chat slice, investigation pipeline slice contracts, `EvidenceEntry`, state-update helpers, and pure defaults.
- `core/domain/types/` — Shared typed contracts for evidence, retrieval, and tool-related payloads.
- `tools/system/watch_dog/` — Watchdog feature: per-threshold Telegram alarm dispatch with cooldown, sitting on top of `integrations/telegram/*`.
- `gateway/http/webapp.py` — Web-facing health app served by the gateway daemon; the `opensre` CLI is `surfaces/cli/__main__.py`.

## 2. Entry Points

### Adding a Tool

The tool registry auto-discovers modules under `tools/`, so the normal path is to add one module or package there and let discovery pick it up. See [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md) for the full file list and the detailed definition of done (package structure, contract/implementation rules, live-payload parsing, required docs/tests).

Steps:

1. Pick the simplest shape that fits the tool. Use a `BaseTool` subclass (from `core.tool_framework.base`) for richer behavior; use `@tool(...)` from `core.tool_framework.tool_decorator` for a lightweight function tool.
2. Declare clear metadata: `name`, `description`, `source`, `input_schema`, and any `use_cases`, `requires`, `outputs`, or `retrieval_controls` you need.
3. Before opening or approving the PR, follow [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md).

### Changing the investigation pipeline

Investigations are coordinated in `tools/investigation/lifecycle.py` and exposed via
`tools/investigation/capability.py`. Semantic stages live under
`tools/investigation/stages/`; reporting lives under
`tools/investigation/reporting/`. See
[docs/investigation-pipeline-architecture.md](docs/investigation-pipeline-architecture.md)
for the end-to-end stage/loop diagrams before making structural changes.

Files to touch:

- `tools/investigation/lifecycle.py` for high-level stage ordering.
- `core/state/` for shared agent state and investigation pipeline slice contracts
  that cross stage boundaries.
- `core/domain/` for pure investigation rules (alert source mapping, tool planning,
  category alignment, correlation scoring).
- `core/` for shared LLM runtime helpers (tool loop and LLM invoke error
  classification).
- `core/state/*.py` when adding or renaming persisted investigation fields
  (update `AgentStateModel` and the matching slice).
- `docs/` — update or add a page if the change introduces user-visible behavior or configuration.
- `tests/` coverage for the affected CLI, synthetic, or integration paths.

Steps:

1. Keep each stage focused on one responsibility.
2. Extend state models when new fields cross stage boundaries.
3. Update tests that exercise `run_investigation` / streaming entry points.

### Adding an Integration

Integration work usually spans config normalization, verification, integration-local clients/helpers, tools, docs, and tests. See [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md) for the full file list, examples from the repo (Datadog, Grafana, Hermes), and the detailed definition of done (core completeness, investigation wiring, docs/tests, `make verify-integrations`, final demo gate).

Steps:

1. Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape.
2. Wire the tool layer after the config path is stable.
3. Before opening or approving the PR, follow [docs/adding-tools-and-integrations.md](docs/adding-tools-and-integrations.md).

## 3. Footguns (common mistakes to avoid)

- No planning-stage fail-closed safeguard (v0.1): the interactive-shell action planner never denies a turn — do **not** reintroduce a planner denial, `mark_unhandled`, or the `UNHANDLED:` convention. Full rationale: [docs/interactive-shell-action-policy.md](docs/interactive-shell-action-policy.md); package rule: `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution").
- Docs navigation: Adding an `.mdx` file under `docs/` is not enough — Mintlify only shows pages listed in `docs/docs.json`. Forgetting the `pages` entry leaves the doc unreachable from the site sidebar.
- Investigation tool schemas: draft-07 JSON Schema (e.g. `"type": ["object", "null"]`) can pass loose checks but fail the LLM API on first invoke because **all** available investigation tools are sent together. Normalize in the provider adapter and extend registry contract tests; see [docs/investigation-tool-calling.md](docs/investigation-tool-calling.md).
- Interactive-shell action selection: do not implement regex/keyword/fuzzy intent routing or deterministic action bypasses around the action-agent path. See `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution") for the full rule and the sanctioned literal-`/slash` exception.
- Information exposure through an exception (CWE-209 / CodeQL `py/stack-trace-exposure`): never send an exception's detail — `str(exc)`, `repr(exc)`, `traceback.format_exc()`, `exc.args`, provider/model/field internals — to an **external surface**. External surfaces are HTTP responses (`JSONResponse`/`HTTPException.detail` in `gateway/http/`) and chat gateway messages delivered to Slack/Telegram users (`OutputSink.render_error` on the gateway sinks). Log full detail server-side (`logger` + `capture_exception`) and return a generic message or `type(exc).__name__` only. The local CLI/terminal sink is **not** external — it may show detail. Redact at the sink/response boundary, not per call site, so the shared turn engine keeps detail for local dev.
- Cyclic imports (CodeQL `py/cyclic-import`): CodeQL counts **function-local** and `TYPE_CHECKING` imports as part of a cycle, so making an import lazy does **not** clear the alert. Break the cycle structurally — move the shared symbol (type, exception, helper) into a **leaf** module both sides import, and never add a back-edge from a lower-level module up to a higher-level one. Precedent: `surfaces/cli/wizard/validation_result.py` and `surfaces/cli/llm_auth/persist.py` exist only to hold shared symbols so `validation` ↔ `azure_openai` and `_ui` → `service` stay acyclic.
- CodeQL does not model `NoReturn`: it treats `pytest.skip`, `pytest.fail`, `sys.exit`, `typer.Exit` and custom raise-helpers as if they return, so any code after them looks reachable. Two alerts come from this — `py/uninitialized-local-variable` when a name is bound in `try` and the `except` only calls such a function, and unreachable-code when a `with` body ends in a bare `raise`. Do **not** silence with a comment: bind the name on every path CodeQL can see. Prefer a sentinel over exception control flow for ordinary "not found" — `next(iterable, None)` plus an explicit `if x is None:` guard, not `try: next(...) except StopIteration:`. `mypy` narrows correctly after the guard because it *does* honour `NoReturn`. For the bare-`raise` case, extract a `_raise()` helper.
- CI typecheck does **not** cover `tests/`: `make typecheck` runs mypy over `PYTHON_SOURCE_PATHS` (`config core gateway integrations platform surfaces tools`) only. Type errors in test files never fail CI, so do not assume a clean `make typecheck` means the tests you just wrote are type-clean — run mypy on the test path directly when it matters.

