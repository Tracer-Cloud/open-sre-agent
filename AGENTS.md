## Tracer Development Reference

## Build and Run commands

- Build `make install` (sets up the project environment via `uv sync` and installs this repo in editable mode)
- Run **`uv run opensre …`** from the repo root while developing — preferred approach, uses this checkout even if another `opensre` is on your `PATH`.
- Use **`uv run python …`** for any Python commands.

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)
- Do not keep compatibility-only forwarding modules after refactors. Once imports and tests
  are migrated, remove the old module path in the same change and use one canonical import path.

Before any push or PR creation follow **[CI.md](CI.md)** — lint, format, typecheck, and test commands all live there.

## 1. Repo Map

| Path                  | What it does                                                                                       |
| --------------------- | -------------------------------------------------------------------------------------------------- |
| `core/`               | Investigation orchestration, context assembly, the shared runtime tool-calling loop, and domain logic (state, types, correlation rules). |
| `cli/`                | Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support.               |
| `interactive_shell/`  | Interactive terminal (REPL) loop, slash commands, chat/help surfaces, action-planning harness, and terminal UI. |
| `integrations/`       | Per-integration config normalization, verification, clients, helpers, store/catalog logic, and the Hermes log pipeline. |
| `tools/`              | Tool registry, decorator, base classes, per-tool packages, and shared tool utilities.              |
| `platform/`           | Cross-cutting platform services: guardrails, masking, sandbox, analytics, auth, notifications, observability. |
| `config/`             | Shared constants, prompts, UI theme, and the web app entrypoint (`config/webapp.py`).              |
| `infra/deployment/`         | Deployment operations, remote-hosted runtime code, and external runtime entrypoints.               |
| `tests/`              | Unit, integration, synthetic, deployment, e2e, chaos engineering, and support tests.               |
| `docs/`               | User-facing documentation, integration guides, and docs-site assets.                               |
| `.github/`            | CI workflows, issue templates, pull request template, and repository automation.                   |
| `Dockerfile`         | Optional production container image (FastAPI health app via uvicorn).                         |
| `pyproject.toml`      | Python project metadata, dependency configuration, tooling, and package settings.                  |
| `Makefile`            | Canonical local automation for install, test, verify, deploy, and cleanup targets.                 |
| `README.md`           | Product overview, install, quick start, high-level capabilities, and links to deeper docs.         |
| `docs/DEVELOPMENT.md` | Contributor workflows: CI parity commands, dev container, benchmark, deployment, telemetry detail. |
| `docs/investigation-tool-calling.md` | Investigation ReAct tool schemas, LLM invoke payloads, and message shapes (all providers). |
| `SETUP.md`            | Machine setup (all platforms, Windows, MCP/OpenClaw, troubleshooting).                             |
| `CI.md`               | Mandatory pre-push checklist: lint, format, typecheck, tests — agents MUST follow before pushing. |
| `TESTING.md`          | `ReplDriver` reference: API, usage patterns, wait-time guide, and limitations.                    |
| `CONTRIBUTING.md`     | Contribution workflow, branch/PR guidance, and quality expectations.                               |

Main packages one level deeper:

- `platform/analytics/` — Analytics event plumbing and install helpers used by the onboarding flow.
- `platform/auth/` — JWT and authentication helpers for local and hosted runtime access.
- `cli/` — Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support.
- `interactive_shell/` — Interactive terminal (TTY) loop, slash-command surface, chat/help handoff, session runtime, and terminal UI. REPL watchdog slash commands (`/watch`, `/watches`, `/unwatch`): PR demo steps live under **Interactive shell: REPL watchdog demo** in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#interactive-shell-repl-watchdog-demo).
- `config/constants/` — Shared prompt and other static constants.
- `core/context/` — Context assembly boundary for building, trimming, ranking, and packaging incident evidence before agent/runtime consumption.
- `infra/deployment/` — Single top-level home for deployment-facing code, split by concern:
    - `infra/deployment/entrypoints/` — SDK and MCP entrypoints exposed to external runtimes.
    - `infra/deployment/operations/` — _Runtime / infra_ around a deployment (health polling, EC2 output files, provider dry-run validation).
    - `infra/deployment/remote/` — Remote-hosted runtime operations and integration points.
- `platform/guardrails/` — Guardrail rules, evaluation engine, audit helpers, and CLI bindings.
- `integrations/` — Integration config normalization, verification, selectors, clients, integration-local helpers, store, and catalog logic.
- `integrations/hermes/` — Hermes log tailing, incident classification, correlator, sinks, and investigation bridge.
- `integrations/llm_cli/` — Subprocess-backed LLM CLIs (e.g. Codex). Extension guide: `integrations/llm_cli/AGENTS.md`.
- `platform/masking/` — Masking utilities for redacting or normalizing sensitive content.
- `tools/investigation/` — Composite investigation capability, public entrypoints, semantic stages, and reporting.
- `core/` — Shared LLM tool-calling loop (execute tools, message shaping, context budget).
- `core/llm/` — Hosted LLM provider clients, retry/schema helpers, and investigation tool-calling adapters.
- `platform/sandbox/` — Sandboxed execution helpers for controlled runtime actions.
- `core/context/state/` — Shared agent runtime envelope (`AgentState`), chat slice, investigation pipeline slice contracts, `EvidenceEntry`, state-update helpers, and pure defaults.
- `tools/` — Tool registry, decorator, base classes, per-tool packages, shared utilities, and registry helpers.
- `core/domain/types/` — Shared typed contracts for evidence, retrieval, and tool-related payloads.
- `platform/` — Guardrails, masking, sandbox, analytics, auth, and cross-cutting platform services (e.g. `platform/notifications/telegram_delivery.py`).
- `tools/watch_dog/` — Watchdog feature: per-threshold Telegram alarm dispatch with cooldown, sitting on top of `platform/notifications/telegram_delivery.py`.
- `config/webapp.py` — Web-facing application entrypoint; the `opensre` CLI is `cli/__main__.py`.

## 2. Entry Points

### Adding a Tool

The tool registry auto-discovers modules under `tools/`, so the normal path is to add one module or package there and let discovery pick it up.

Files to touch:

- `tools/<ToolName>/__init__.py` for the tool implementation, or `tools/<tool_file>.py` for a lighter-weight function tool.
- `tools/utils/` if the tool needs shared helper code.
- `integrations/<name>/client.py` if the tool should reuse a dedicated integration API client instead of inlining requests.
- `docs/<tool_name>.mdx` for user-facing usage, parameters, and examples.
- `docs/docs.json` — add the page path (without `.mdx`) to the appropriate `pages` array so Mintlify navigation includes it.
- `tests/tools/test_<tool_name>.py` for behavior and regression coverage.

Steps:

1. Pick the simplest shape that fits the tool. Use a `BaseTool` subclass for richer behavior; use `@tool(...)` from `tools.tool_decorator` for a lightweight function tool.
2. Declare clear metadata: `name`, `description`, `source`, `input_schema`, and any `use_cases`, `requires`, `outputs`, or `retrieval_controls` you need.
3. Keep the tool self-contained. Put reusable transport or integration-specific parsing code in `integrations/<name>/` or shared tool glue in `tools/utils/` rather than copying it into the tool body.
4. If the tool should appear in both investigation and chat surfaces, set `surfaces=("investigation", "chat")`.
5. Add tests that cover schema shape, availability, extraction, and the runtime behavior that the planner depends on.
6. Before opening or approving the PR, follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) for tool/integration-specific wiring, payload, docs, and regression checks.

### Changing the investigation pipeline

Investigations are coordinated in `tools/investigation/lifecycle.py` and exposed via
`tools/investigation/capability.py`. Semantic stages live under
`tools/investigation/stages/`; reporting lives under
`tools/investigation/reporting/`.

Files to touch:

- `tools/investigation/lifecycle.py` for high-level stage ordering.
- `core/context/` for shared context assembly, trimming, ranking, and evidence-envelope logic
  that runs before agent/runtime consumption.
- `core/domain/` for pure investigation rules (alert source mapping, tool planning,
  category alignment, correlation scoring).
- `core/` for shared LLM runtime helpers (tool loop and LLM invoke error
  classification).
- `core/context/state/*.py` when adding or renaming persisted investigation fields
  (update `AgentStateModel` and the matching slice).
- `docs/` — update or add a page if the change introduces user-visible behavior or configuration.
- `tests/` coverage for the affected CLI, synthetic, or integration paths.

Steps:

1. Keep each stage focused on one responsibility.
2. Extend state models when new fields cross stage boundaries.
3. Update tests that exercise `run_investigation` / streaming entry points.

### Adding an Integration

Integration work usually spans config normalization, verification, integration-local clients/helpers, tools, docs, and tests.

Files to touch:

- `integrations/<name>/__init__.py` for config builders, validators, selectors, and normalization helpers.
- `integrations/<name>/client.py` when the integration needs a dedicated API client.
- `integrations/<name>/verifier.py` when the integration needs local verification logic.
- `integrations/catalog.py` when the new integration must be resolved into the shared runtime config.
- `integrations/verify.py` when the integration needs a local verification path.
- `tools/<Name>Tool/` or `tools/<tool_file>.py` for the user-facing tool layer.
- `docs/<name>.mdx` for user-facing setup, usage, and verification docs.
- `docs/docs.json` — add the page path (without `.mdx`) to the appropriate `pages` array so Mintlify navigation includes it.
- `tests/integrations/test_<name>.py` for config, verification, and store coverage.
- `tests/tools/test_<tool_name>.py` and any relevant `tests/e2e/` or `tests/synthetic/` files if the integration is exercised by tools or scenarios.

Treat `integrations/` as the canonical user/config and external-client boundary, and `tools/` as the canonical agent-callable boundary. Do not add or import top-level `vendors/` or `services/` packages.

Examples from the repo:

- Datadog: `integrations/datadog/client.py`, `integrations/datadog/verifier.py`, `integrations/catalog.py`, `tools/datadog_tools/`, and Datadog-related tests under `tests/integrations/` and `tests/tools/`.
- Grafana: `integrations/grafana/`, `integrations/catalog.py`, `tools/grafana_tools/`, `cli/wizard/local_grafana_stack/`, and the Grafana-related tests under `tests/integrations/`.
- Hermes: `integrations/hermes/`, `tools/HermesLogsTool/`, `tools/HermesSessionEvidenceTool/`, `cli/commands/hermes.py`, `tests/hermes/`, and `tests/synthetic/hermes/`.

Basic steps:

1. Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape.
2. Add or update the integration-local client only when the integration needs direct remote calls.
3. Wire the tool layer after the config path is stable.
4. Add docs and tests together so the integration is understandable and verifiable.
5. Run `make verify-integrations` before treating the integration as complete.
6. Before opening or approving the PR, follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) for integration completeness, investigation wiring, docs, and demo/test requirements.

## 3. Rules (if X -> do Y)

- If core agent or pipeline logic changes -> run `make test-cov` and `make typecheck`.
- If a new feature is shipped (tool, CLI command, pipeline behavior, integration) -> add a `docs/` page or section covering usage, configuration, and examples before the PR is opened.
- If a new `docs/` page is added or renamed -> register it in `docs/docs.json` under the correct `pages` array in the same PR (path without `.mdx`, e.g. `messaging/whatsapp` for `docs/messaging/whatsapp.mdx`).
- If an existing feature changes behavior, flags, or config shape -> update the relevant `docs/` page in the same PR; docs and code must stay in sync.
- When writing or editing a `docs/` page -> write for **users, not contributors**. Open with a command quick-reference table (command | what it does) if the page covers CLI commands. Follow with brief practical examples. Keep internal file formats, JSONL schemas, and implementation details out of user-facing pages — move those to `docs/DEVELOPMENT.md` or a contributor-only reference file if truly needed.
- If a tool's API or schema changes -> update docs in `docs/` and update the related unit tests, usually under `tests/tools/`. For investigation LLM tool-calling (any provider), follow [docs/investigation-tool-calling.md](docs/investigation-tool-calling.md).
- If adding or materially changing a tool/integration -> follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) in the same PR.
- If an integration changes -> update `tests/integrations/` and verify with `make verify-integrations`.
- If adding a new integration -> follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) before opening the PR for review.
- If adding new tests -> always place them in `tests/`, never inside the source packages (no inline tests).
- If CI-only tests are added -> mark them with the right pytest marker or place them in the appropriate e2e/synthetic/chaos folder so they do not run in the default local suite.
- If investigation branching or loop behavior changes -> update `tools/investigation/lifecycle.py` and the tests for that path.
- If adding or changing interactive REPL behavior (slash commands, session management, display output) -> use `ReplDriver` from `tests/utils/repl_driver.py` for live verification alongside unit tests; see [TESTING.md](TESTING.md).
- If pushing or creating a PR -> follow the full pre-push checklist in [CI.md](CI.md).

## 4. Testing

Test commands, turn-handling rules, CI-only paths: **[CI.md](CI.md)**. Live REPL testing with `ReplDriver`: **[TESTING.md](TESTING.md)**.

## 5. Footguns (common mistakes to avoid)

- No planning-stage fail-closed safeguard (v0.1): the interactive-shell action planner never denies a turn with "I couldn't safely decide actions". All terminal actions are read-only, so unmatched/ambiguous/chatty clauses run what they can and fall through to the assistant. Do **not** reintroduce a planner denial, the `mark_unhandled` tool, or the `UNHANDLED:` convention. Rationale and details: `core/agent_harness/AGENTS.md` and `docs/interactive-shell-action-policy.md`. If mutating actions are ever added, gate them at the execution stage (`tools/interactive_shell/shared/execution_policy.py`), not the planner.
- Vendored deps: No obvious vendored third-party dependencies are present. Python dependencies are managed in `pyproject.toml`, and the docs site has its own `docs/package.json` and `docs/pnpm-lock.yaml`. Do not vendor new libraries unless there is a strong reason.
- Secrets: Never commit `.env` - always use `.env.example` as the template. Use read-only credentials for production integrations.
- CI-only tests: Some e2e tests, including Kubernetes, EKS, and chaos engineering paths, require live infrastructure and are excluded from `make test-cov`. Do not expect them to pass locally without that environment.
- Legacy graph dev server: removed; use `make dev` for a local uvicorn hint or run investigations via the CLI.
- Docker requirement: Several targets, including the Grafana local stack and Chaos Mesh workflows, require a running Docker daemon.
- Docs navigation: Adding an `.mdx` file under `docs/` is not enough — Mintlify only shows pages listed in `docs/docs.json`. Forgetting the `pages` entry leaves the doc unreachable from the site sidebar.
- Investigation tool schemas: draft-07 JSON Schema (e.g. `"type": ["object", "null"]`) can pass loose checks but fail the LLM API on first invoke because **all** available investigation tools are sent together. Normalize in the provider adapter and extend registry contract tests; see [docs/investigation-tool-calling.md](docs/investigation-tool-calling.md).
- External-system code: `integrations/` owns config, clients, verifiers, and integration-local helpers; `tools/` owns every `@tool(...)` function and `BaseTool` class. Do not reintroduce top-level `vendors/` or `services/` packages.
- Compatibility shims: Do not leave modules whose only job is to re-export symbols from a new
  location. Update callers to the canonical module and delete the old path.
- Interactive-shell action selection: do not implement regex/keyword/fuzzy
  intent routing, literal slash-command shortcuts, or deterministic action
  bypasses around the action-agent AgentTool path. Engineers have been fired
  before for implementing this exact shortcut. The runtime's literal-`/slash`
  detection (`input_policy._literal_slash_command_text`) is terminal UI policy
  only (spinner/stdin gating), not an execution path.

## 6. New Integration Checklist

Follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) — it is the single definition of done for all tool and integration work.
