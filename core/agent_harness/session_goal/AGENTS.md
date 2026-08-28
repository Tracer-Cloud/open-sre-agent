# session_goal/ — `/goal` / SessionGoal component

Host-scoped completion across many `chat` turns. **Not** the ReAct
`Goal` / `turns/goal_review.py`.

## Leaves (import the leaf you need)

| Module | Owns |
|--------|------|
| `goal.py` | `SessionGoal`, statuses/reasons, attach/clear, reason derive |
| `evaluate.py` | Structured host completion (claim ≠ proof) |
| `confirm.py` | Optional LLM confirm after tool-evidence achieve |
| `progress.py` | `SESSION_GOAL_PROGRESS_MARK` + progress / status-line formatting |
| `continuation.py` | Session-goal continuation prompts |
| `persist.py` | Flush / restore payload |
| `run_until.py` | `run_until_session_goal` |

Do **not** import progress/continuation/persist/evaluate/confirm/run_until from
`goal.py` (avoids `py/cyclic-import`). Callers import the leaf, or curated
names from `session_goal` package `__init__`.

## Borders

- User vocabulary = `/goal` only (see progress constants).
- Attach via `/goal set`, explicit `goal=`, or structured handoff — never
  user-text keyword routing.
- Shell multi-step ≠ this component (`prompts/action/multi_step_policy.py`).
- L0/L1 evidence only couples at finalize (Want-me-to / CTA suppress).

Design SoT (local notes): `opensre-notes/session-goal-opensre-aug2026.html`.
