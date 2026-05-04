"""Tests for `.github/scripts/good_first_issue_assign.py`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "good_first_issue_assign.py"


@pytest.fixture(scope="module")
def gfi():
    spec = importlib.util.spec_from_file_location("good_first_issue_assign", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _label(name: str) -> dict:
    return {"name": name}


def test_screen_skips_pull_request_comment_thread(gfi):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [],
            "user": {"login": "alice"},
            "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/1"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "NONE",
        },
    }
    assert gfi.screen_event_without_api(event) == "comment_on_pull_request"


def test_screen_skips_without_label(gfi):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label("bug")],
            "assignees": [],
            "user": {"login": "alice"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "NONE",
        },
    }
    assert gfi.screen_event_without_api(event) == "not_good_first_issue"


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_screen_skips_repo_insider(gfi, association):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [],
            "user": {"login": "alice"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": association,
        },
    }
    assert gfi.screen_event_without_api(event) == "commenter_repo_insider"


def test_screen_allows_contributor_with_zero_merges_checked_later(gfi):
    """CONTRIBUTOR is allowed here; merged-PR count is enforced in assign_decision."""
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [],
            "user": {"login": "alice"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "CONTRIBUTOR",
        },
    }
    assert gfi.screen_event_without_api(event) is None


def test_screen_skips_already_assignee(gfi):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [{"login": "bob"}],
            "user": {"login": "alice"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "FIRST_TIMER",
        },
    }
    assert gfi.screen_event_without_api(event) == "already_assignee"


def test_screen_skips_when_issue_claimed_by_other(gfi):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [{"login": "alice"}],
            "user": {"login": "maintainer"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "NONE",
        },
    }
    assert gfi.screen_event_without_api(event) == "issue_already_claimed"


def test_screen_allows_none_association(gfi):
    event = {
        "issue": {
            "state": "open",
            "labels": [_label(gfi.GOOD_FIRST_LABEL)],
            "assignees": [],
            "user": {"login": "alice"},
        },
        "comment": {
            "user": {"login": "bob", "type": "User"},
            "author_association": "NONE",
        },
    }
    assert gfi.screen_event_without_api(event) is None


def test_assign_decision_skips_merged_prs(gfi):
    ok, reason = gfi.assign_decision(
        skip_reason_pre_api=None,
        merged_pr_count_for_commenter=1,
    )
    assert ok is False
    assert reason == "has_merged_prs"


def test_assign_decision_ok_zero_merges(gfi):
    ok, reason = gfi.assign_decision(
        skip_reason_pre_api=None,
        merged_pr_count_for_commenter=0,
    )
    assert ok is True
    assert reason == ""


def test_build_notice_short(gfi):
    body = gfi.build_assign_notice_body(assignee_login="bob")
    assert body == "@bob You've been **assigned** to this issue. Thanks for picking it up."
