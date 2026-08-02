from __future__ import annotations

import importlib.util
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


def test_a_truncated_file_list_refuses_the_merge() -> None:
    """``gh`` pages the file list at 100 and does not say so.

    Observed on a real PR: ``changedFiles`` 105 against a ``files`` length of
    100 — and that PR touched both ``core/`` and ``gateway/``. Trusting the short
    list would let the hidden runtime change merge as if the PR were docs.
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


def test_process_pr_requests_changed_files_from_github() -> None:
    """The completeness guard is inert unless the query asks for the count."""
    # Arrange
    source = _MODULE_PATH.read_text(encoding="utf-8")

    # Act
    json_fields = source.split('"--json",', 1)[1].split("]", 1)[0]

    # Assert
    assert "changedFiles" in json_fields
    assert "files" in json_fields


def _protected_pr_payload() -> dict:
    return {
        "baseRefName": "main",
        "state": "OPEN",
        "isDraft": False,
        "labels": [{"name": "automerge"}],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "changedFiles": 2,
        "files": [{"path": "docs/a.mdx"}, {"path": "core/agent/agent.py"}],
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
        "title": "a change",
    }


def test_a_green_protected_pr_is_not_merged(monkeypatch) -> None:
    """The policy has to hold in ``main``, not only in the helper.

    Every other test here checks ``_protected_paths`` directly, which stays green
    even if the caller stops consulting it. This pins the refusal on the path
    that actually merges.
    """
    # Arrange
    merges: list = []
    monkeypatch.setattr(automerge_pr, "_run_gh", lambda _args: _protected_pr_payload())
    monkeypatch.setattr(
        automerge_pr.subprocess, "run", lambda *args, **_kwargs: merges.append(args[0])
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "77")

    # Act
    exit_code = automerge_pr.main()

    # Assert
    assert exit_code == 0
    assert merges == [], "a PR touching core/ was auto-merged"


def test_a_green_ordinary_pr_is_still_merged(monkeypatch) -> None:
    """The guard must not block everything it was not aimed at."""
    # Arrange
    merges: list = []
    payload = _protected_pr_payload()
    payload["files"] = [{"path": "docs/a.mdx"}, {"path": "docs/b.mdx"}]
    monkeypatch.setattr(automerge_pr, "_run_gh", lambda _args: payload)
    monkeypatch.setattr(
        automerge_pr.subprocess, "run", lambda *args, **_kwargs: merges.append(args[0])
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR_NUMBER", "78")

    # Act
    automerge_pr.main()

    # Assert
    assert len(merges) == 1
    assert "merge" in merges[0]
