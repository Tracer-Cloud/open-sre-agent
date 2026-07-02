# Large Refactor Definition of Done

Use this checklist for any refactor that **consolidates or re-homes
behavior across multiple surfaces or packages** — e.g. collapsing
per-surface initialization into a shared class, centralizing session or
config handling, or merging duplicated logic that has diverged across
`interactive_shell/`, `gateway/`, `tools/investigation/`, `core/`, etc.

It does not apply to a localized bug fix or a single-file cleanup — use it
when the change touches "every surface does X its own way" style problems
(the T-2/T-3 `agent_harness` consolidation series is the reference case).
Use it together with [AGENTS.md](AGENTS.md) and [CI.md](CI.md).

## 1. Before writing code

- [ ] Re-read the issue/design doc against the **current** tree, not the tree it
      was written against. Refactor issues rot fast — file paths, package
      names, and even which surfaces exist can drift within days. Grep for
      every path and class name the issue references and confirm it still
      exists (or find its current equivalent) before planning around it.
- [ ] Enumerate every call site that currently owns the behavior being
      consolidated, per surface, with `file:function` references — not just
      the ones named in the issue. Duplicated logic tends to have silently
      diverged (different error handling, an extra cache, a missing case) —
      note the differences, don't assume they're identical copies.
- [ ] Check the dependency chain: is this refactor blocked on another
      open issue/PR that hasn't landed? If a prerequisite is still in flight,
      decide explicitly whether to (a) wait, (b) build the consolidated
      class against today's per-surface state and re-point it once the
      prerequisite lands, or (c) do the minimum of the prerequisite inline.
      Don't silently assume a "should be done first" step is done.
- [ ] Check who else is assigned to this issue or its prerequisites, and
      whether a branch/PR already exists for it (`gh pr list --search
      "<keyword>"`). Large refactors are easy to collide on.
- [ ] Identify the architectural invariant the refactor must preserve or
      establish (e.g. a one-way import boundary between packages) and find
      the test that enforces it, or note that one needs to be added.

## 2. Design

- [ ] Prefer one canonical construction/entry-point pattern over adding a
      second one. If the codebase already has a documented pattern for this
      class of problem (e.g. `AGENTS.md` "Pattern A" for agent construction),
      extend it — don't introduce a parallel mechanism.
- [ ] Plan the migration as incremental per-surface cutovers, not a single
      big-bang change that rewrites every surface at once. Each surface
      should be swappable to the new consolidated path independently, with
      the old per-surface code removed in the same commit that migrates it
      (no dead code left "just in case").
- [ ] No compatibility shims or forwarding modules for the old per-surface
      logic once a surface is migrated. Delete it in the same change per
      [AGENTS.md](AGENTS.md)'s no-compat-shim rule.
- [ ] Decide the rollback story before merging: since there are no
      compat shims, rollback means reverting the commit/PR, not toggling a
      flag. Confirm this is acceptable for the affected surfaces (does
      anything depend on the old behavior being live in production between
      merge and full rollout?).

## 3. Implementation

- [ ] Land the shared/consolidated class or module first, covered by tests,
      before touching any surface's call site.
- [ ] Migrate one surface per commit (or per small PR) where practical, so a
      regression in surface B doesn't block or get bundled with surface A's
      migration.
- [ ] Remove the per-surface logic being replaced in the same change that
      migrates that surface — don't leave both paths live.
- [ ] Update or add the import-boundary / architecture test that encodes the
      invariant from step 1, so a future change can't silently reintroduce
      the per-surface pattern this refactor removed.

## 4. Verification

- [ ] `make test-cov` and `make typecheck` pass (required for any core
      agent/pipeline change per [AGENTS.md](AGENTS.md)).
- [ ] Existing tests for every migrated surface still pass unmodified in
      behavior (not just unmodified in assertions — re-read what they assert
      if the consolidation changed timing/ordering of side effects like
      integration resolution or session load).
- [ ] Exercise at least one real (non-mocked) path per migrated surface —
      e.g. an interactive-shell session via `ReplDriver`
      ([TESTING.md](TESTING.md)), a gateway dispatch, an investigation run —
      to confirm the consolidated path behaves the same as the old
      per-surface path end to end.

## 5. Docs and closeout

- [ ] Update the relevant package `AGENTS.md` (e.g.
      `core/agent_harness/AGENTS.md`) to describe the new consolidated
      pattern and explicitly forbid the old per-surface pattern, the same way
      existing "Do NOT reintroduce X" notes are written.
- [ ] Update `docs/DEVELOPMENT.md` or other contributor docs if the
      refactor changes a documented architecture boundary.
- [ ] Check off acceptance criteria on the source issue against what was
      actually shipped, not what was planned — note any criteria that ended
      up out of scope and why.
