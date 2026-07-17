# CLI surface (`surfaces/cli/`)

These instructions apply to `surfaces/cli/` and all subdirectories. Repo-root
[`AGENTS.md`](../../AGENTS.md) and [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
still apply.

## Purpose

`surfaces/cli/` is the stateless `opensre <command>` runner: Click commands,
onboarding wizard, investigation CLI streaming, LLM auth helpers, and terminal
rendering for batch workflows. It composes `core/`, `tools/`, and
`integrations/`; it does not own investigation orchestration or provider SDKs.

Runtime LLM provider wiring lives in [`core/llm/AGENTS.md`](../../core/llm/AGENTS.md).

## Package map

| Path | Owns | Keep out |
| --- | --- | --- |
| `__main__.py`, `group.py`, `invocation.py` | CLI entry, command group wiring | Feature logic |
| `commands/` | Click command definitions (`investigate`, `onboard`, `doctor`, …) | Wizard flow, provider API calls, investigation pipeline |
| `wizard/` | Interactive onboarding: provider pick, credentials, integrations | Investigation stages, REPL loop |
| `wizard/flow.py` | Wizard stage order and repick loop | Provider-specific API logic |
| `wizard/_ui.py` | Shared prompts, steps, `_choose_model`, Rich output | Provider-, vendor-, or integration-specific behavior |
| `wizard/validation.py` | Generic `validate_provider_credentials` dispatch | Per-provider validation bodies (delegate out) |
| `wizard/config.py` | `ProviderOption` / integration metadata catalogs | Live API calls |
| `wizard/<provider>.py`, `wizard/local_llm/` | One provider or feature area (e.g. `azure_openai.py`, Ollama) | Unrelated providers |
| `wizard/configurators/`, `wizard/integration_validators/` | Per-integration onboarding UI and health checks | LLM provider runtime |
| `llm_auth/` | CLI auth login / API-key persistence for providers | Provider client construction |
| `investigation/` | CLI streaming wrapper around `tools/investigation/` | Pipeline stage logic |
| `ui/renderer/` | Terminal progress / report rendering for investigate | Wizard prompts |
| `error_mapping.py` | Map exceptions to user-facing CLI messages | Provider-specific error strings (prefer `core/` helpers) |
| `lifecycle/` | Install/update/uninstall helpers | Onboarding |

## File placement rules

1. **Provider-specific onboarding** (Azure, Ollama, future hosted providers): add or extend
   `wizard/<provider>.py` or `wizard/<feature>/` — not `wizard/_ui.py` or
   `wizard/validation.py`. Those files dispatch only.
2. **Integration-specific onboarding**: use `wizard/configurators/<area>.py` and matching
   `wizard/integration_validators/` — not `wizard/flow.py`.
3. **New Click command**: add `commands/<name>.py` and register in `commands/__init__.py`.
   Keep commands thin; call into `tools/`, `wizard/`, or `core/` as needed.
4. **Two or more functions for the same provider, vendor, or integration** in wizard →
   dedicated module first (same threshold as root `AGENTS.md`).
5. **Do not add provider `if provider.value == ...` blocks** to `_ui.py`, `flow.py`, or
   generic `validation.py`. Extract a specialist module and delegate.

## Examples

| Task | Put it here |
| --- | --- |
| Azure deployment picker + credential validation | `wizard/azure_openai.py` |
| Ollama install / hardware probes | `wizard/local_llm/` |
| Runtime LiteLLM kwargs for Azure | `core/llm/providers/azure_openai.py` |
| New `opensre foo` subcommand | `commands/foo.py` |
| Investigate terminal progress UI | `ui/renderer/` |
| Shared model picker for all API providers | `wizard/_ui.py` (`_choose_model`) |

## Tests

Mirror layout under `tests/cli/` (`tests/cli/wizard/`, `tests/cli/commands/`, …).
