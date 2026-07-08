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

## Subprocess runner decoupling (T-03)

Subprocess runners must stay split into two layers:

1. **Pure execution** under `tools/interactive_shell/` — spawn, communicate,
   planning, structured results. No Rich, no `surfaces.interactive_shell.ui`,
   no `execution_confirm`.
2. **REPL presentation** under `surfaces/interactive_shell/runtime/subprocess_runner/`
   — `ReplSubprocessPresenter` implements `SubprocessPresenter` and is injected
   through `ActionToolContext.subprocess_presenter` from `action_turn.py`.

Shared stdlib-only helpers live in `tools/interactive_shell/subprocess.py`.
Rich stream relay stays in `surfaces/.../subprocess_runner/task_streaming.py`.

**Do NOT reintroduce** `surfaces.interactive_shell.ui` or
`surfaces.interactive_shell.runtime.subprocess_runner` imports in:

- `shell/runner.py`
- `synthetic/runner.py`
- `implementation/claude_code_executor.py`
- `actions/cli_command.py`

Enforced by `tests/tools/interactive_shell/test_import_boundaries.py`.

## Dependency direction

```text
surfaces/interactive_shell (ui, dispatch, ReplSubprocessPresenter)
  -> tools/interactive_shell (action-tool implementations)
    -> core/agent_harness (session types, ports)
```

`tools/interactive_shell/` **may** depend on:

- `core.agent_harness.session` runtime types (`Session`, `TaskKind`,
  `TaskRecord`, `TaskStatus`) for session/task bookkeeping.
- `integrations/` when an action tool wraps an integration client (e.g. Claude
  Code in `implementation/claude_code_executor.py`).

It must **not**:

- Be imported by `core/agent_harness/` (enforced by import-boundary tests).
- Import `surfaces.interactive_shell.*` from subprocess runners (see T-03 list
  above). Other action tools (`slash`, `investigation`, etc.) may still reach
  into the surface temporarily — that is separate T-4 debt.
- Grow eager submodule imports in `__init__.py` (keep the explicit-import
  discipline documented there; several tool modules import back into
  `command_registry`, so eager imports here reintroduce circular imports).

## Remaining surface coupling (T-4 debt, out of T-03 scope)

Several non-subprocess action tools still import `surfaces.interactive_shell.ui`
or `command_registry` directly (`slash.py`, `investigation.py`, etc.). Do not
add new surface imports there without a port; subprocess runners are the
reference pattern going forward.
