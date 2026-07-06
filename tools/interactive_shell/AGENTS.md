# tools/interactive_shell/ package rules

These instructions apply to `tools/interactive_shell/` and all of its
subdirectories. The repo-root `AGENTS.md` still applies.

## Purpose

This package hosts the **action-tool implementations** the agent harness calls
during an interactive-shell turn — the concrete `run` bodies behind the action
tools listed in `core/agent_harness/tools/action_tools.py`:

- `actions/` — the action tools themselves (`shell_run`, `cli_exec`,
  `slash_invoke`, `code_implement`, `investigation_start`, `alert_sample`,
  `assistant_handoff`, `llm_set_provider`, `synthetic_run`, `task_cancel`).
- `shell/` — shell command parsing, execution policy, and the
  `run_shell_command`/`run_cd`/`run_pwd` runner behind `actions/shell.py`.
- `synthetic/` — the synthetic-test runner behind `actions/synthetic.py`.
- `implementation/` — the `/implement` (Claude Code) launcher.
- `shared/` — cross-tool helpers (e.g. investigation launch, `allow_tool`).
- `contracts` — imported by `command_registry.slash_catalog` during early import
  wiring; see the `__init__.py` docstring for why tool submodules must be
  imported explicitly rather than eagerly here (circular-import avoidance).

Per the repo-root `AGENTS.md`, `tools/` owns every `@tool(...)` function and
`RegisteredTool`/`BaseTool` class; this package is the interactive-shell slice of
that ownership.

## Dependency direction

The one-way boundary is:

```text
surfaces/interactive_shell (ui, dispatch)
  -> tools/interactive_shell (action-tool implementations)
    -> core/agent_harness (session types, ports)
```

`tools/interactive_shell/` **may** depend on:

- `core.agent_harness.session` runtime types (`Session`, `TaskKind`,
  `TaskRecord`, `TaskStatus`) for session/task bookkeeping.
- `surfaces.interactive_shell.runtime.subprocess_runner.*` for streamed
  subprocess execution used by the shell / CLI / synthetic / implement tools.

It must **not**:

- Be imported by `core/agent_harness/` (that boundary is enforced by
  `tests/core/agent/test_import_boundaries.py`).
- Grow eager submodule imports in `__init__.py` (keep the explicit-import
  discipline documented there; several tool modules import back into
  `command_registry`, so eager imports here reintroduce circular imports).

### Known coupling to narrow (follow-up, not yet enforced)

Several tools currently reach into `surfaces.interactive_shell.ui`
(theme constants, `execution_confirm.execution_allowed`, table/print helpers)
and `surfaces.interactive_shell.command_registry` (`slash.py` /
`task_cancel.py` / `llm_provider.py` dispatch a slash command as a tool). This
is a deliberate short-term coupling, not the target shape:

- UI rendering and slash dispatch are surface concerns. The intended direction
  is for the surface to inject a narrow callback/port (status output,
  confirmation, dispatch) that these tools call, so the tool layer does not
  import `ui` or `command_registry` directly.
- The clearest offender is `synthetic/runner.py`, which imports
  `surfaces.interactive_shell.ui` only to print status. Prefer replacing that
  with a surface-provided status callback when this module is next touched.

Do not add *new* `surfaces.interactive_shell.ui` / `command_registry` imports
here without a strong reason; prefer a port passed in from the surface.
