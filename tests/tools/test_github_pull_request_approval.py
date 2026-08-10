"""The pull-request approval prompt.

This is the last thing a human reads before a write reaches GitHub, so the
properties pinned here are about the decision, not the formatting: the reviewer
must see the repository and base branch they are committing against, every file
the commit touches, and whether the PR will skip required checks — without the
prompt growing unbounded on a big change set.
"""

from __future__ import annotations

from typing import Any

import pytest

from integrations.github.tools.pull_request_approval import PULL_REQUEST_APPROVAL_DISPLAY

_ARGUMENTS: dict[str, Any] = {
    "owner": "acme",
    "repo": "platform",
    "base_branch": "main",
    "head_branch": "fix/drop-orphan-subscription",
    "title": "fix(pubsub): drop the orphaned subscription",
    "body": "**What:** remove the placeholder.\n\n**Why:** it has no consumer.",
    "changes": [
        {"path": "terraform/modules/functions/notifications.tf", "content": "x" * 2445},
        {"path": "terraform/modules/functions/legacy.tf", "delete": True},
    ],
}

_RESULT = {
    "pull_request_number": 2604,
    "pull_request_url": "https://github.com/acme/platform/pull/2604",
}


class TestHeadline:
    def test_names_the_action_and_the_title(self) -> None:
        headline = PULL_REQUEST_APPROVAL_DISPLAY.headline(_ARGUMENTS)
        assert headline == "Create PR — fix(pubsub): drop the orphaned subscription"

    def test_a_missing_title_still_produces_a_line(self) -> None:
        assert PULL_REQUEST_APPROVAL_DISPLAY.headline({}) == "Create PR — (untitled)"

    def test_a_long_title_is_truncated(self) -> None:
        headline = PULL_REQUEST_APPROVAL_DISPLAY.headline({"title": "z" * 500})
        assert len(headline) < 200
        assert headline.endswith("…")

    def test_a_multiline_title_stays_on_one_line(self) -> None:
        assert "\n" not in PULL_REQUEST_APPROVAL_DISPLAY.headline({"title": "one\ntwo"})


class TestDetails:
    def test_repository_and_both_branches_lead(self) -> None:
        """Approving the wrong base branch is the expensive mistake."""
        lines = PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS).splitlines()
        assert lines[0] == "acme/platform"
        assert lines[1] == "main  ←  fix/drop-orphan-subscription"

    def test_every_changed_path_is_listed(self) -> None:
        details = PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS)
        assert "2 files changed" in details
        assert "terraform/modules/functions/notifications.tf" in details
        assert "terraform/modules/functions/legacy.tf" in details

    def test_file_contents_are_reported_by_size_not_pasted(self) -> None:
        details = PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS)
        assert "2,445 chars written" in details
        assert "x" * 200 not in details

    def test_deletion_is_called_out(self) -> None:
        assert "legacy.tf — deleted" in PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS)

    def test_the_description_explains_why(self) -> None:
        details = PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS)
        assert "remove the placeholder" in details
        assert "it has no consumer" in details

    def test_a_long_description_is_trimmed(self) -> None:
        details = PULL_REQUEST_APPROVAL_DISPLAY.details({**_ARGUMENTS, "body": "why " * 400})
        assert len(details) < 1000
        assert "2 files changed" in details

    def test_draft_is_flagged_because_checks_will_not_run(self) -> None:
        details = PULL_REQUEST_APPROVAL_DISPLAY.details({**_ARGUMENTS, "draft": True})
        assert "draft" in details
        assert "checks will not run" in details

    def test_a_non_draft_says_nothing_about_drafts(self) -> None:
        assert "draft" not in PULL_REQUEST_APPROVAL_DISPLAY.details(_ARGUMENTS)

    def test_a_long_change_set_reports_the_remainder(self) -> None:
        changes = [{"path": f"module/file{index}.tf", "content": "x"} for index in range(30)]
        details = PULL_REQUEST_APPROVAL_DISPLAY.details({**_ARGUMENTS, "changes": changes})
        assert "30 files changed" in details
        assert "… and 18 more" in details

    def test_a_single_file_is_singular(self) -> None:
        changes = [{"path": "a.tf", "content": "x"}]
        assert "1 file changed" in PULL_REQUEST_APPROVAL_DISPLAY.details(
            {**_ARGUMENTS, "changes": changes}
        )

    @pytest.mark.parametrize(
        "changes",
        [None, [], "terraform/main.tf", {"path": "a.tf"}],
        ids=["none", "empty", "string", "mapping"],
    )
    def test_a_malformed_change_set_renders_rather_than_raising(self, changes: Any) -> None:
        """The tool rejects these itself; the prompt must not blow up first."""
        details = PULL_REQUEST_APPROVAL_DISPLAY.details({**_ARGUMENTS, "changes": changes})
        assert "no files listed" in details

    def test_an_entry_without_contents_is_named_as_such(self) -> None:
        changes = [{"path": "a.tf"}]
        details = PULL_REQUEST_APPROVAL_DISPLAY.details({**_ARGUMENTS, "changes": changes})
        assert "a.tf — no contents supplied" in details

    def test_empty_arguments_do_not_raise(self) -> None:
        assert "(repository not set)" in PULL_REQUEST_APPROVAL_DISPLAY.details({})


class TestReceipt:
    def test_reports_the_number_and_the_link(self) -> None:
        receipt = PULL_REQUEST_APPROVAL_DISPLAY.receipt(_ARGUMENTS, _RESULT)
        assert receipt.startswith("Create PR #2604 — fix(pubsub): drop the orphaned subscription")
        assert receipt.endswith("https://github.com/acme/platform/pull/2604")

    @pytest.mark.parametrize(
        "result",
        [
            {},
            {"ok": False, "error": "branch exists", "pull_request_number": 0},
            {"pull_request_number": 2604, "pull_request_url": ""},
        ],
        ids=["empty", "failed", "no-url"],
    )
    def test_no_receipt_without_a_pull_request(self, result: dict[str, Any]) -> None:
        """An empty receipt keeps the original headline rather than inventing one."""
        assert PULL_REQUEST_APPROVAL_DISPLAY.receipt(_ARGUMENTS, result) == ""
