# Investigation Context

`context/` owns the provider-agnostic investigation context that OpenSRE passes
between alert intake, evidence gathering, diagnosis, and reporting stages.

Use this package for typed investigation state, evidence records, incident
context envelopes, and the policies that decide which incident facts are carried
forward. In this package, "context" means investigation evidence/state, not REPL
session state, CLI prompt grounding, or generic agent-runtime request assembly.

## Belongs Here

- Investigation state slices and their Pydantic validation model.
- Incident evidence entries, provenance, and evidence-envelope contracts.
- Context budget, trimming, ranking, and summarization policies for incident evidence.
- Provider-agnostic assembly logic that packages data from `core/`,
  `integrations/`, and `tools/` for investigation stages.

## Does Not Belong Here

- Agent orchestration or stage sequencing; keep that in `tools/investigation/`.
- The LLM/tool-calling loop and runtime request contracts; keep those in `core/`.
- Terminal UI, REPL session state, prompt history, CLI help, AGENTS.md grounding,
  and slash commands; keep those in `interactive_shell/`.
- External clients, config normalization, and verification; keep those in
  `integrations/`.
- Agent-callable tool implementations; keep those in `tools/`.
- Platform services such as guardrails, masking, auth, telemetry, notifications,
  and sandboxing; keep those in `platform/`.

## Naming Rule

New names in this package should make the investigation boundary obvious. Prefer
terms such as `incident`, `evidence`, `provenance`, `retrieval`, and `state`.
Avoid adding generic `prompt`, `session`, `runtime`, or `grounding` modules here;
those concepts belong to their owning surface or runtime package.
