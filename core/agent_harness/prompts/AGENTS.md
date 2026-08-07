# prompts/ — agent prompt assembly

Packages are split by **agent path**, not by PromptTier folders.

## Layout

| Package | Role |
|---------|------|
| `kernel/` | `PromptEnvelope` / tiers / `SurfaceProfile` — no agent-path knowledge |
| `grounding/` | Prompt-side grounding providers (`DefaultPromptContextProvider`) that feed assemblers — distinct from harness `grounding/` caches |
| `assistant/` | Conversational assistant (parts → contributors → envelope → turn) |
| `action/` | Tool-calling / slash action-agent prompts |
| `gather/` | Evidence-gather system prompts |
| `memory/` | Conversation window + prior-investigation recall |
| `runtime_facts/` | Runtime-metadata fact lines for prompts |
| `skills/` | Progressive skill index + markdown bodies (`loader.py` + `*.md`) |
| `rules.py` | Shared rule fragments (leaf) |

Root `__init__.py` is a thin facade for common imports.

## Dependency rule (acyclic)

```
kernel  ←  memory, runtime_facts, skills, rules, grounding
        ↑
   assistant, action, gather   (peer agent packages — never import each other)
```

- Leaves may import `kernel` (and each other only when a clear owner exists).
- Agent packages may import leaves + `kernel`.
- **Do not** add `assistant` ↔ `action` ↔ `gather` imports.

## Provenance

`PromptBlock.provenance` should name the owning module under this tree
(e.g. `core.agent_harness.prompts.action.text`).
