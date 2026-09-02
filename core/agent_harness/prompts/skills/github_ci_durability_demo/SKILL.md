---
name: github-ci-durability-demo
description: >-
  Run a disposable, falsifiable GitHub CI durability demo: create a same-repo
  PR with two sequential failures, invoke fix_github_pr_ci exactly once,
  classify the result, and clean up. Multi-step; load before acting.
---
══════════════════════════════════════════════════════════
GITHUB CI DURABILITY DEMO SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE:
- The user explicitly asks to demonstrate, test, or reproduce whether the
  GitHub CI fixer stops after one repair when a later dependent check fails.
- The user asks for the "CI durability demo" or to create a deliberately
  broken, disposable CI pull request for this experiment.

DO NOT USE THIS SKILL FOR:
- Fixing a real failing PR. Load `github-ci-fix` instead.
- CI onboarding. Load `github-ci-fix-onboarding` instead.
- Creating a broken PR without an explicit demo request.

EXPERIMENT CONTRACT:
- This is an opt-in synthetic experiment, not evidence manufactured to force
  a predetermined conclusion. Stage two is ordinary, fixable code and is
  visible in the PR, but its CI job waits for stage one to pass.
- Invoke `fix_github_pr_ci` exactly once. Never prompt it to ignore stage two,
  never hide repository files from it, and never rerun it inside the same
  experiment. A fixer that repairs both stages has disproved the limitation for
  this run.
- Run only against a checkout whose authenticated user has write access. Never
  target a fork, protected branch, existing PR, or existing branch.
- The fixture helper accepts only the generated
  `codex/ci-durability-demo-*` branch and an OpenSRE marker in the PR body.
- Cleanup is mandatory after evidence collection, including setup, tool, or
  verification failures. Do not leave the intentionally red PR open.
- Keep tokens out of commands and output. Use existing `gh` authentication and
  OpenSRE's configured GitHub integration.

Step labeling rules (UX):
- Before every numbered step's tool calls, emit `### [n/8] <step name>` and one
  short status sentence in the same response.
- After results arrive, start the outcome line with ✓ or ✗ before moving
  to the next step. Never renumber or skip the cleanup header.

Steps, in order:

1. **Readiness**
   Use `shell_run` for read-only checks: `gh --version`, `gh auth status`, the
   checkout's GitHub origin, and the coding-agent readiness probe from
   `github-ci-fix-onboarding`. Stop before mutation if any prerequisite fails.

2. **Create disposable PR**
   Explain that this step creates and pushes a unique branch and opens an
   intentionally failing PR. Then call one approval-gated `shell_run` from the
   target checkout:

   `uv run python core/agent_harness/prompts/skills/github_ci_durability_demo/demo_fixture.py create --repo-root "$(git rev-parse --show-toplevel)"`

   Parse the JSON result. Preserve `state_file`, `worktree`, `pr_number`,
   `pr_url`, `branch`, `baseline_sha`, and `repo` for later steps. If creation
   fails after returning a `state_file`, continue directly to cleanup.

3. **Observe first failure**
   Call exactly `github_cli(args=["pr", "checks", "<pr_number>", "--json",
   "name,state,bucket,link"], repo="<repo>")`. Repeat only while
   `durability-stage-one` is pending. Proceed as soon as that check is terminal;
   do not wait for Greptile or other review apps. Require
   `durability-stage-one` to fail and `durability-stage-two` to be skipped or
   absent. If a completed repository CI check unrelated to the demo fails,
   classify the run inconclusive and continue to cleanup without invoking the
   fixer. Never request a `checkSuites` field; `gh pr view` does not support it.

4. **Capture baseline**
   Call exactly `github_cli(args=["pr", "view", "<pr_number>", "--json",
   "headRefOid,commits", "--jq", "{headRefOid: .headRefOid, commitCount:
   (.commits | length)}"], repo="<repo>")`. Record the SHA and commit count;
   do not change the PR and do not substitute unsupported JSON fields.

5. **Run one fixer attempt**
   Explain that the tested operation edits, commits, and pushes. Call exactly:
   `fix_github_pr_ci(pr_url="<pr_url>", workspace="<worktree>")`.
   This skill owns the surrounding experiment, so do not apply the
   `github-ci-fix` skill's "output response_text and stop" rule here.

6. **Collect final evidence**
   After the fixer returns, call the same exact `pr checks` command from step 3
   and the same exact `pr view` command from step 4. The fixer already waits for
   post-push checks; do not add `--watch`, do not request `checkSuites`, and do
   not call `fix_github_pr_ci` again.

7. **Clean up**
   Always call the approval-gated helper, even when an earlier step failed:

   `uv run python core/agent_harness/prompts/skills/github_ci_durability_demo/demo_fixture.py cleanup --state-file "<state_file>"`

   The helper refuses cleanup unless the branch prefix, temporary worktree,
   repository, and PR marker all identify this demo. It closes the PR, deletes
   the generated remote/local branch, removes its worktree, and deletes the
   temporary state file. If one operation fails, it still attempts every
   independent cleanup step and retains the state file for a safe retry. Resolve
   the reported blocker and repeat cleanup until the result has `ok=true`.

8. **Report verdict**
   Report one of these exact verdict labels, followed by PR URL, before/after
   SHAs, commits added, first and final check states, fixer response, and cleanup
   result:
   - `LIMITATION REPRODUCED`: one fixer commit made stage one pass, stage two
     then failed, and the single fixer invocation returned without another fix.
   - `LIMITATION NOT REPRODUCED`: all demo stages passed after the one fixer
     invocation (for example, it anticipated and fixed both defects).
   - `INCONCLUSIVE`: setup/tool/infra/unrelated-CI failure prevented the
     experiment from distinguishing those outcomes.

Never generalize one run into "the agent cannot make durable fixes." State only
what this run demonstrates and link the evidence PR, even though it is closed.
