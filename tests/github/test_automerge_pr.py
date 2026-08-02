from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "automerge_pr.py"
_spec = importlib.util.spec_from_file_location("automerge_pr", _MODULE_PATH)
assert _spec and _spec.loader
automerge_pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(automerge_pr)


def test_squash_commit_subject_appends_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): show full root cause", "3025")
        == "fix(cli): show full root cause (#3025)"
    )


def test_squash_commit_subject_avoids_duplicate_pr_number() -> None:
    assert (
        automerge_pr._squash_commit_subject("fix(cli): example (#3025)", "3025")
        == "fix(cli): example (#3025)"
    )


def test_checks_are_green_for_completed_check_runs() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "windows test",
                "status": "COMPLETED",
                "conclusion": "SKIPPED",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_checks_are_green_for_successful_status_contexts() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            }
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_checks_are_green_mixed_check_run_and_status_context() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "SUCCESS",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_status_context_failure_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "FAILURE",
            }
        ]
    )
    assert green is False
    assert reason == "status not green: ci/legacy (FAILURE)"


def test_status_context_pending_blocks_merge() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "StatusContext",
                "context": "ci/legacy",
                "state": "PENDING",
            }
        ]
    )
    assert green is False
    assert reason == "status still pending: ci/legacy"


def test_ignores_automerge_workflow_check_while_running() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Merge when CI is green",
                "workflowName": "Auto-merge",
                "status": "IN_PROGRESS",
                "conclusion": None,
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_ignores_mintlify_vale_spellcheck_failure() -> None:
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Mintlify Validation (tracer) - vale-spellcheck",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_ignores_greptile_review_while_running() -> None:
    """Greptile is external; waiting on it strands PRs after the last Actions retry."""
    green, reason = automerge_pr._checks_are_green(
        [
            {
                "__typename": "CheckRun",
                "name": "quality (ubuntu-latest)",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "Greptile Review",
                "status": "IN_PROGRESS",
                "conclusion": None,
            },
        ]
    )
    assert green is True
    assert reason == "all checks green"


def test_behind_but_clean_pr_is_updated_not_stalled() -> None:
    """A PR blocked only by being out of date should take the latest base.

    With "require branches to be up to date" on, every merge to main marks the
    other open PRs ``BEHIND``. Auto-merge used to stop there, so a human had to
    press "Update branch" and wait for CI — on a busy repo a PR could go stale
    again before it finished.
    """
    # Arrange
    pr = {"mergeStateStatus": "BEHIND", "mergeable": "MERGEABLE"}

    # Act / Assert
    assert automerge_pr._needs_branch_update(pr) is True


def test_conflicted_pr_is_not_auto_updated() -> None:
    """A real conflict needs a human; updating the branch cannot resolve it."""
    # Arrange
    pr = {"mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}

    # Act / Assert
    assert automerge_pr._needs_branch_update(pr) is False


def test_ready_pr_is_not_updated() -> None:
    """A PR that can merge now must not be pushed back through CI."""
    # Arrange
    pr = {"mergeStateStatus": "CLEAN", "mergeable": "MERGEABLE"}

    # Act / Assert
    assert automerge_pr._needs_branch_update(pr) is False


def test_blocked_pr_is_not_mistaken_for_out_of_date() -> None:
    """``BLOCKED`` means checks or review are outstanding, not staleness."""
    # Arrange
    pr = {"mergeStateStatus": "BLOCKED", "mergeable": "MERGEABLE"}

    # Act / Assert
    assert automerge_pr._needs_branch_update(pr) is False


def _pr(**overrides: object) -> dict:
    """A labeled, open, green PR targeting main."""
    pr = {
        "baseRefName": "main",
        "state": "OPEN",
        "isDraft": False,
        "labels": [{"name": "automerge"}],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
        "title": "a change",
        "changedFiles": 1,
        "files": [{"path": "docs/a.mdx"}],
    }
    pr.update(overrides)
    return pr


def _drive(monkeypatch, prs: dict, *, on_update=None, on_merge=None) -> list:
    """Run a sweep over ``prs`` (number -> payload), recording actions taken."""
    actions: list = []
    monkeypatch.setattr(automerge_pr, "_labeled_open_pr_numbers", lambda _repo: list(prs))
    monkeypatch.setattr(automerge_pr, "_run_gh", lambda args: prs[args[2]])

    def _update(_repo, number):
        actions.append(("update", number))
        if on_update:
            on_update(number)

    def _merge(*args, **_kwargs):
        actions.append(("merge", args[0][3]))
        if on_merge:
            on_merge(args[0][3])

    monkeypatch.setattr(automerge_pr, "_update_branch", _update)
    monkeypatch.setattr(automerge_pr.subprocess, "run", _merge)
    automerge_pr._sweep("o/r")
    return actions


def test_a_pr_that_cannot_be_refreshed_does_not_strand_the_queue(monkeypatch) -> None:
    """A fork that disallows maintainer edits answers update-branch with 403.

    Aborting the sweep there would leave every PR behind it stale until someone
    noticed, and would fail the workflow on every merge to main.
    """
    # Arrange
    prs = {n: _pr(mergeStateStatus="BEHIND") for n in ("101", "102", "103")}

    def _forbidden(number):
        if number == "101":
            raise subprocess.CalledProcessError(1, "gh pr update-branch", stderr="403 Forbidden")

    # Act
    actions = _drive(monkeypatch, prs, on_update=_forbidden)

    # Assert
    assert actions == [("update", "101"), ("update", "102")]


def test_sweep_advances_one_pr_then_stops(monkeypatch) -> None:
    """One mutation per sweep; merging pushes main, which drives the next pass."""
    # Arrange
    prs = {n: _pr(mergeStateStatus="BEHIND") for n in ("201", "202", "203")}

    # Act
    actions = _drive(monkeypatch, prs)

    # Assert
    assert actions == [("update", "201")]


def test_a_red_pr_is_never_refreshed(monkeypatch) -> None:
    """Refreshing a failing PR re-runs the whole suite and never converges."""
    # Arrange
    failing = [
        {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "FAILURE"}
    ]
    prs = {"301": _pr(mergeStateStatus="BEHIND", statusCheckRollup=failing)}

    # Act
    actions = _drive(monkeypatch, prs)

    # Assert
    assert actions == []


def test_queue_is_served_oldest_first(monkeypatch) -> None:
    """The PR that has waited longest is the one advanced."""
    # Arrange
    listed = [
        {"number": 402, "createdAt": "2026-08-02T10:00:00Z"},
        {"number": 401, "createdAt": "2026-07-30T09:00:00Z"},
        {"number": 403, "createdAt": "2026-08-01T11:00:00Z"},
    ]
    monkeypatch.setattr(automerge_pr, "_run_gh", lambda _args: listed)

    # Act
    order = automerge_pr._labeled_open_pr_numbers("o/r")

    # Assert
    assert order == ["401", "403", "402"]


def test_runtime_and_ci_paths_are_never_auto_merged() -> None:
    """The blast-radius paths need a human on the merge button.

    ``core``, ``platform`` and ``gateway`` carry the agent runtime, the
    multi-tenant platform and the chat gateway; a bad change reaches every user.
    The workflow and script paths are the merge machinery itself, which must not
    be able to widen its own permissions unattended.
    """
    # Arrange
    protected = [
        "core/agent/react_loop.py",
        "platform/guardrails/engine.py",
        "gateway/slack/handler.py",
        ".github/workflows/ci.yml",
        ".github/scripts/automerge_pr.py",
    ]

    # Act / Assert
    for path in protected:
        assert automerge_pr._protected_paths([{"path": path}]) == [path], path


def test_ordinary_paths_stay_eligible() -> None:
    """Docs, tests and integrations keep merging without a human."""
    # Arrange
    files = [
        {"path": "docs/quickstart.mdx"},
        {"path": "tests/core/test_thing.py"},
        {"path": "integrations/datadog/client.py"},
        {"path": "surfaces/cli/app.py"},
        {"path": "README.md"},
    ]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == []


def test_one_protected_file_taints_the_whole_pr() -> None:
    """A mostly-docs PR that also edits the runtime still needs a human."""
    # Arrange
    files = [{"path": "docs/a.mdx"}, {"path": "core/agent/agent.py"}, {"path": "docs/b.mdx"}]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == ["core/agent/agent.py"]


def test_lookalike_prefixes_are_not_protected() -> None:
    """Only real package roots count, not names that merely start the same."""
    # Arrange
    files = [{"path": "coreutils/x.py"}, {"path": "platformer/y.py"}, {"path": "gateways.md"}]

    # Act / Assert
    assert automerge_pr._protected_paths(files) == []


def test_protected_pr_is_neither_merged_nor_refreshed(monkeypatch) -> None:
    """Refusing early also keeps it out of the queue's single action slot."""
    # Arrange
    prs = {
        "501": _pr(mergeStateStatus="BEHIND", files=[{"path": "core/agent/agent.py"}]),
        "502": _pr(mergeStateStatus="BEHIND", files=[{"path": "docs/a.mdx"}]),
    }

    # Act
    actions = _drive(monkeypatch, prs)

    # Assert
    assert actions == [("update", "502")]


def test_process_pr_requests_changed_files_from_github(monkeypatch) -> None:
    """Without ``changedFiles`` in the view query the truncation guard is dead."""
    # Arrange
    seen: list[str] = []

    def _capture(args: list[str]):
        seen.extend(args)
        return _pr()

    def _noop_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(automerge_pr, "_run_gh", _capture)
    monkeypatch.setattr(automerge_pr.subprocess, "run", _noop_run)

    # Act
    automerge_pr._process_pr("o/r", "1")

    # Assert
    json_fields = seen[seen.index("--json") + 1]
    assert "changedFiles" in json_fields.split(",")
    assert "files" in json_fields.split(",")


def test_a_truncated_file_list_refuses_the_merge() -> None:
    """``gh`` pages the file list at 100 and does not say so.

    A 105-file PR reports every path in ``changedFiles`` but only the first 100
    in ``files``. Trusting the short list would let a ``core/`` change past the
    cut merge as if the PR were docs, so the check has to fail closed. Observed
    on a real PR: ``changedFiles`` 105 against a ``files`` length of 100.
    """
    # Arrange
    pr = {"changedFiles": 105, "files": [{"path": f"docs/{i}.mdx"} for i in range(100)]}

    # Act / Assert
    assert automerge_pr._file_list_is_complete(pr) is False


def test_a_complete_file_list_is_accepted() -> None:
    """The common case: every changed path is visible."""
    # Arrange
    pr = {"changedFiles": 3, "files": [{"path": "docs/a.mdx"}] * 3}

    # Act / Assert
    assert automerge_pr._file_list_is_complete(pr) is True


def test_a_missing_count_does_not_block_everything() -> None:
    """Without ``changedFiles`` there is nothing to compare; fall back to the list."""
    # Arrange
    pr = {"files": [{"path": "docs/a.mdx"}]}

    # Act / Assert
    assert automerge_pr._file_list_is_complete(pr) is True


def test_a_large_pr_is_not_merged_or_refreshed(monkeypatch) -> None:
    """The refusal happens before any mutation, like the protected-path one."""
    # Arrange
    prs = {
        "601": _pr(
            mergeStateStatus="BEHIND",
            changedFiles=105,
            files=[{"path": f"docs/{i}.mdx"} for i in range(100)],
        ),
        "602": _pr(mergeStateStatus="BEHIND"),
    }

    # Act
    actions = _drive(monkeypatch, prs)

    # Assert
    assert actions == [("update", "602")]


def test_a_full_page_without_a_count_is_refused() -> None:
    """A missing count plus a full page is indistinguishable from truncation."""
    # Arrange
    pr = {"files": [{"path": f"docs/{i}.mdx"} for i in range(automerge_pr.FILE_PAGE_SIZE)]}

    # Act / Assert
    assert automerge_pr._file_list_is_complete(pr) is False


def test_a_short_list_without_a_count_is_accepted() -> None:
    """Below the page size there is nothing that could have been cut off."""
    # Arrange
    pr = {"files": [{"path": "docs/a.mdx"}]}

    # Act / Assert
    assert automerge_pr._file_list_is_complete(pr) is True
