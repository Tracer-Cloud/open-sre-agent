# prompts/ — single-agent prompt assembly

## Layout

| Package | Role |
|---------|------|
| `kernel/` | `PromptEnvelope` / tiers / `SurfaceProfile` — no agent-path knowledge |
| `grounding/` | Prompt-side grounding providers (`DefaultPromptContextProvider`) that feed assemblers — distinct from harness `grounding/` caches |
| `action/` | Tool-calling agent prompts (`opensre_system_prompt.md` is the system base) |
| `memory/` | Conversation window + prior-investigation recall |
| `runtime_facts/` | Runtime-metadata fact lines for prompts |
| `skills/` | Progressive skill index + markdown bodies (`loader.py` + `*.md`) |
| `rules.py` | Shared rule fragments (leaf) |
| `system_prompt.py` | Loader for ``action/opensre_system_prompt.md`` — the shared system base |

Root `__init__.py` is a thin facade for common imports.

## Dependency rule (acyclic)

```
kernel  ←  memory, runtime_facts, skills, rules, grounding, system_prompt
        ↑
      action
```

- Leaves may import `kernel` (and each other only when a clear owner exists).
- The action package may import leaves + `kernel`.

## Provenance

`PromptBlock.provenance` should name the owning module under this tree
(e.g. `core.agent_harness.prompts.action.opensre_system_prompt.md`).
