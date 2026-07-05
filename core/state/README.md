# Agent & Investigation State

`core/state/` owns the provider-agnostic agent and investigation state that OpenSRE
carries between alert intake, evidence gathering, diagnosis, and reporting stages.

Use this package for typed investigation state, evidence records, and the
state-update helpers that decide which incident facts are carried forward. This is
**state** — not REPL session state, CLI prompt grounding, or generic agent-runtime
request assembly.

## Belongs Here

- The shared `AgentState` runtime envelope and its Pydantic validation model.
- Investigation pipeline slice contracts and the chat-mode slice.
- Incident evidence entries (`EvidenceEntry`), provenance, and evidence contracts.
- State-update helpers and pure defaults.

## Does Not Belong Here

- Agent orchestration or stage sequencing; keep that in `tools/investigation/`.
- Context trimming, ranking, and budget logic; keep that in `core/context_budget.py`.
- The LLM/tool-calling loop and runtime request contracts; keep those in sibling
  `core/` runtime modules.
- Terminal UI, REPL session state, prompt history, CLI help, AGENTS.md grounding,
  and slash commands; keep those in `surfaces/interactive_shell/`.
- External clients, config normalization, and verification; keep those in
  `integrations/`.
- Agent-callable tool implementations; keep those in `tools/`.
- Platform services such as guardrails, masking, auth, telemetry, notifications,
  and sandboxing; keep those in `platform/`.

## Also exported (temporary)

- ``MutableAgentState`` and related harness session types in ``agent_state.py``.
  These live here for historical import paths; target home is
  ``core/agent_harness/session/`` ([#3685](https://github.com/Tracer-Cloud/opensre/issues/3685)).

## Naming Rule

New names here should make the state boundary obvious. Prefer terms such as
`state`, `slice`, `evidence`, `provenance`, and `snapshot`. Avoid adding generic
`prompt`, `session`, `runtime`, or `grounding` modules here; those belong to their
owning surface or runtime package.
