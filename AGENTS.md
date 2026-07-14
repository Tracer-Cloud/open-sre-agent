## OpenSRE Development Reference

## Build and Run commands

- Build `make install` (sets up the project environment via `uv sync` and installs this repo in editable mode)
- Run **`uv run opensre …`** from the repo root while developing — preferred approach, uses this checkout even if another `opensre` is on your `PATH`.
- Use **`uv run python …`** for any Python commands.

## Code Style

- Use strict typing, follow DRY principle
- One clear purpose per file (separation of concerns)
- Do not keep compatibility-only forwarding modules after refactors. Once imports and tests
  are migrated, remove the old module path in the same change and use one canonical import path.

Before any push or PR creation follow [**CI.md**](CI.md) — lint, format, typecheck, and test commands all live there.

When opening a PR, fill out the [**PR template**](.github/PULL_REQUEST_TEMPLATE.md) — it is not optional boilerplate; it has a required AI-usage disclosure section.

## 1. Repo Map

| Path                                          | What it does                                                                                                                                                                                                                                                                                                                           |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `core/`                                       | Investigation orchestration, context assembly, the shared runtime tool-calling loop, and domain logic (state, types, correlation rules). Includes `core/tool_framework/` — the `BaseTool` base class, `@tool` decorator, registered-tool primitives, error telemetry, skill-guidance helpers, and shared payload utilities (`utils/`). |
| `surfaces/cli/`                               | Command-line interface, onboarding wizard, local LLM helpers, and CLI tests support.                                                                                                                                                                                                                                                   |
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
| `TESTING.md`                                  | `ReplDriver` reference: API, usage patterns, wait-time guide, and limitations.                                                                                                                                                                                                                                                         |
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

The tool registry auto-discovers modules under `tools/`, so the normal path is to add one module or package there and let discovery pick it up. See [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) for the full file list and the detailed definition of done (package structure, contract/implementation rules, live-payload parsing, required docs/tests).

Steps:

1. Pick the simplest shape that fits the tool. Use a `BaseTool` subclass (from `core.tool_framework.base`) for richer behavior; use `@tool(...)` from `core.tool_framework.tool_decorator` for a lightweight function tool.
2. Declare clear metadata: `name`, `description`, `source`, `input_schema`, and any `use_cases`, `requires`, `outputs`, or `retrieval_controls` you need.
3. Before opening or approving the PR, follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md).

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

Integration work usually spans config normalization, verification, integration-local clients/helpers, tools, docs, and tests. See [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md) for the full file list, examples from the repo (Datadog, Grafana, Hermes), and the detailed definition of done (core completeness, investigation wiring, docs/tests, `make verify-integrations`, final demo gate).

Steps:

1. Add the integration config and normalization logic first so the rest of the stack can consume a consistent shape.
2. Wire the tool layer after the config path is stable.
3. Before opening or approving the PR, follow [TOOL_INTEGRATION_CHECKLIST.md](TOOL_INTEGRATION_CHECKLIST.md).

## 3. Footguns (common mistakes to avoid)

- No planning-stage fail-closed safeguard (v0.1): the interactive-shell action planner never denies a turn — do **not** reintroduce a planner denial, `mark_unhandled`, or the `UNHANDLED:` convention. Full rationale: [docs/interactive-shell-action-policy.md](docs/interactive-shell-action-policy.md); package rule: `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution").
- Docs navigation: Adding an `.mdx` file under `docs/` is not enough — Mintlify only shows pages listed in `docs/docs.json`. Forgetting the `pages` entry leaves the doc unreachable from the site sidebar.
- Investigation tool schemas: draft-07 JSON Schema (e.g. `"type": ["object", "null"]`) can pass loose checks but fail the LLM API on first invoke because **all** available investigation tools are sent together. Normalize in the provider adapter and extend registry contract tests; see [docs/investigation-tool-calling.md](docs/investigation-tool-calling.md).
- Interactive-shell action selection: do not implement regex/keyword/fuzzy intent routing or deterministic action bypasses around the action-agent path. See `surfaces/interactive_shell/AGENTS.md` ("Action Selection And Execution") for the full rule and the sanctioned literal-`/slash` exception.

