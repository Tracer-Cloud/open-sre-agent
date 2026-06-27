# Context

`context/` is the top-level home for OpenSRE context assembly.

Use this package for code that gathers, normalizes, trims, and packages the incident context an agent needs before it reasons or calls tools. The goal is to make context a first-class architectural boundary instead of scattering context-building logic across orchestration, runtime, integrations, tools, and the interactive shell.

## Belongs here

- Provider-agnostic context builders and envelopes.
- Context budget, trimming, ranking, and summarization policies.
- Shared contracts for the evidence bundle passed into agent/runtime code.
- Composition logic that assembles data from `core/`, `integrations/`, and `tools/` without owning those layers.

## Does not belong here

- Agent orchestration or stage sequencing; keep that in `core/orchestration/`.
- The LLM/tool-calling loop; keep that in `core/runtime/`.
- External clients, config, and verification; keep those in `integrations/`.
- Agent-callable tool implementations; keep those in `tools/`.
- Terminal UI, REPL session state, and slash commands; keep those in `interactive_shell/`.
- Platform services such as guardrails, masking, auth, telemetry, notifications, and sandboxing; keep those in `platform/`.
