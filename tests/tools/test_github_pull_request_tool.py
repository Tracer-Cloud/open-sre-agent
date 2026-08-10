"""Tests for ``open_github_pull_request`` — the clone-free PR write path.

The tool exists because the gateway container has no ``git`` and no ``gh``, so
these tests never touch a filesystem or a subprocess: a fake REST client records
every call and returns canned Git Data API payloads.

Two properties matter more than the happy path and are pinned individually:

* the tool can only ever **create** a ref, so no argument combination lets it
  write to the base branch or move a branch that already exists; and
* when the commit lands but the pull request does not, the caller is told the
  branch is on the remote — otherwise a retry hits "branch already exists" and
  the real failure disappears.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest

from integrations.github.client import GitHubApiError
from integrations.github.tools import pull_request as pr_tool
from integrations.github.tools.pull_request import open_github_pull_request
from tests.tools.conftest import BaseToolContract

BASE_COMMIT_SHA = "aaaa111"
BASE_TREE_SHA = "tree000"
NEW_TREE_SHA = "tree999"
NEW_COMMIT_SHA = "bbbb222"

OWNER = "acme"
REPO = "platform"
BASE_BRANCH = "main"
HEAD_BRANCH = "fix/drop-orphan-subscription"

_HEAD_REF_PATH = f"/repos/{OWNER}/{REPO}/git/ref/heads/{HEAD_BRANCH}"
_BASE_REF_PATH = f"/repos/{OWNER}/{REPO}/git/ref/heads/{BASE_BRANCH}"


class _FakeClient:
    """Records requests and answers them from a canned routing table.

    ``missing`` names paths that should answer 404 (an absent ref); ``fail``
    maps a path to the :class:`GitHubApiError` it should raise instead.
    """

    def __init__(
        self,
        *,
        missing: tuple[str, ...] = (_HEAD_REF_PATH,),
        fail: dict[str, GitHubApiError] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._missing = set(missing)
        self._fail = fail or {}

    def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, **_kw: Any
    ) -> Any:
        self.calls.append((method.upper(), path, body))
        if path in self._fail:
            raise self._fail[path]
        if method.upper() == "GET" and path in self._missing:
            raise GitHubApiError("Not Found", status_code=HTTPStatus.NOT_FOUND, path=path)
        return self._response(method.upper(), path)

    def _response(self, method: str, path: str) -> Any:
        if method == "GET" and path.startswith(f"/repos/{OWNER}/{REPO}/git/ref/heads/"):
            return {"object": {"sha": BASE_COMMIT_SHA}}
        if method == "GET" and path.startswith(f"/repos/{OWNER}/{REPO}/git/commits/"):
            return {"sha": BASE_COMMIT_SHA, "tree": {"sha": BASE_TREE_SHA}}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": NEW_TREE_SHA}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": NEW_COMMIT_SHA}
        if method == "POST" and path.endswith("/git/refs"):
            return {"ref": f"refs/heads/{HEAD_BRANCH}"}
        if method == "POST" and path.endswith("/pulls"):
            return {"html_url": f"https://github.com/{OWNER}/{REPO}/pull/7", "number": 7}
        raise AssertionError(f"unexpected request: {method} {path}")

    def bodies(self, method: str, suffix: str) -> list[dict[str, Any]]:
        return [
            body or {}
            for call_method, path, body in self.calls
            if call_method == method and path.endswith(suffix)
        ]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """Install a fake REST client and a token the tool can resolve."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fake = _FakeClient()

    def _build_client(_token: str | None = None) -> _FakeClient:
        return fake

    monkeypatch.setattr(pr_tool, "GitHubRestClient", _build_client)
    return fake


def _registered_tool() -> Any:
    """The registry object behind the decorated function (mypy-opaque attribute)."""
    return open_github_pull_request.__opensre_registered_tool__  # type: ignore[attr-defined]


def _call(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "owner": OWNER,
        "repo": REPO,
        "base_branch": BASE_BRANCH,
        "head_branch": HEAD_BRANCH,
        "title": "Drop the orphaned subscription",
        "body": "The `sub` suffix has no consumer.",
        "changes": [{"path": "terraform/main.tf", "content": "resource {}\n"}],
    }
    kwargs.update(overrides)
    return open_github_pull_request(**kwargs)


class TestOpenGitHubPullRequestContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _registered_tool()


def test_metadata_gates_the_write_behind_approval() -> None:
    """A tool that writes to a repository must not run unattended."""
    registered = _registered_tool()
    assert registered.requires_approval is True
    assert registered.approval_reason
    assert registered.side_effect_level == "mutating"


def test_registered_on_the_action_surface() -> None:
    """Chat turns resolve tools by the 'action' surface; anything else is unreachable."""
    registered = _registered_tool()
    assert [str(surface) for surface in registered.surfaces] == ["action"]


def test_opens_a_pull_request_from_a_new_branch(client: _FakeClient) -> None:
    # Act
    result = _call()

    # Assert
    assert result["ok"] is True, result
    assert result["pull_request_url"] == f"https://github.com/{OWNER}/{REPO}/pull/7"
    assert result["pull_request_number"] == 7
    assert result["commit_sha"] == NEW_COMMIT_SHA
    assert result["files_changed"] == ["terraform/main.tf"]
    assert result["error_kind"] == ""


def test_commit_is_built_on_the_base_tree_and_parented_to_its_tip(client: _FakeClient) -> None:
    """Anything else silently drops every file the change set did not name."""
    # Act
    _call()

    # Assert
    tree_body = client.bodies("POST", "/git/trees")[0]
    assert tree_body["base_tree"] == BASE_TREE_SHA
    assert tree_body["tree"] == [
        {
            "path": "terraform/main.tf",
            "mode": "100644",
            "type": "blob",
            "content": "resource {}\n",
        }
    ]
    commit_body = client.bodies("POST", "/git/commits")[0]
    assert commit_body["parents"] == [BASE_COMMIT_SHA]
    assert commit_body["tree"] == NEW_TREE_SHA


def test_creates_the_ref_and_never_updates_one(client: _FakeClient) -> None:
    """The only write to a ref is a POST creating the head branch."""
    # Act
    _call()

    # Assert
    ref_writes = [
        (method, path, body)
        for method, path, body in client.calls
        if method in {"POST", "PATCH", "PUT"} and "/git/ref" in path
    ]
    assert ref_writes == [
        (
            "POST",
            f"/repos/{OWNER}/{REPO}/git/refs",
            {"ref": f"refs/heads/{HEAD_BRANCH}", "sha": NEW_COMMIT_SHA},
        )
    ]
    assert not any(method == "PATCH" for method, _path, _body in client.calls)


def test_commit_message_defaults_to_the_title(client: _FakeClient) -> None:
    # Act
    _call(commit_message="   ")

    # Assert
    assert client.bodies("POST", "/git/commits")[0]["message"] == "Drop the orphaned subscription"


def test_delete_entry_becomes_a_null_sha_tree_entry(client: _FakeClient) -> None:
    """A null sha removes the path; an empty string would commit an empty file."""
    # Act
    result = _call(changes=[{"path": "terraform/orphan.tf", "delete": True}])

    # Assert
    assert result["ok"] is True, result
    assert client.bodies("POST", "/git/trees")[0]["tree"] == [
        {"path": "terraform/orphan.tf", "mode": "100644", "type": "blob", "sha": None}
    ]


def test_draft_is_forwarded_to_github(client: _FakeClient) -> None:
    # Act
    _call(draft=True)

    # Assert
    assert client.bodies("POST", "/pulls")[0]["draft"] is True


def test_pull_requests_are_ready_for_review_unless_asked_otherwise(client: _FakeClient) -> None:
    """Required status checks never run on a draft, so the PR arrives unverified."""
    # Act
    _call()

    # Assert
    assert client.bodies("POST", "/pulls")[0]["draft"] is False


def test_the_draft_field_tells_the_model_why_to_leave_it_alone() -> None:
    """The default is already false; a model that picks draft anyway needs the reason."""
    # Arrange
    schema = _registered_tool().input_schema

    # Assert
    assert "draft" not in schema["required"]
    assert "checks" in schema["properties"]["draft"]["description"]


def test_head_branch_equal_to_base_is_refused_before_any_write(client: _FakeClient) -> None:
    """The guard that keeps the tool off the base branch."""
    # Act
    result = _call(head_branch=BASE_BRANCH)

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "invalid_branch_name"
    assert not [call for call in client.calls if call[0] == "POST"]


@pytest.mark.parametrize(
    "branch",
    ["fix/has a space", "fix/..escape", "fix/trailing/", "fix/caret^", "fix/at@{x}"],
    ids=["space", "dotdot", "trailing-slash", "caret", "at-brace"],
)
def test_unusable_branch_names_are_refused(client: _FakeClient, branch: str) -> None:
    """Reject locally rather than letting GitHub answer with an opaque 422."""
    # Act
    result = _call(head_branch=branch)

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "invalid_branch_name"
    assert not [call for call in client.calls if call[0] == "POST"]


def test_existing_head_branch_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """No force-update path: an occupied branch name is a hard stop."""
    # Arrange
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fake = _FakeClient(missing=())  # every ref resolves, so the head branch exists
    monkeypatch.setattr(pr_tool, "GitHubRestClient", lambda _token=None: fake)

    # Act
    result = _call()

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "branch_exists"
    assert not [call for call in fake.calls if call[0] == "POST"]


def test_missing_base_branch_is_named_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fake = _FakeClient(missing=(_HEAD_REF_PATH, _BASE_REF_PATH))
    monkeypatch.setattr(pr_tool, "GitHubRestClient", lambda _token=None: fake)

    # Act
    result = _call()

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "base_branch_not_found"
    assert BASE_BRANCH in result["error"]


@pytest.mark.parametrize(
    "changes",
    [
        [],
        "terraform/main.tf",
        [{"path": "terraform/main.tf"}],
        [{"path": "terraform/main.tf", "content": None}],
        [{"content": "x"}],
        [
            {"path": "terraform/main.tf", "content": "a"},
            {"path": "terraform/main.tf", "content": "b"},
        ],
    ],
    ids=["empty", "not-a-list", "no-content", "null-content", "no-path", "duplicate-path"],
)
def test_malformed_change_sets_are_rejected(client: _FakeClient, changes: Any) -> None:
    """A missing content field usually means a diff was attempted; fail loudly."""
    # Act
    result = _call(changes=changes)

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] in {"invalid_changes", "no_changes"}
    assert not [call for call in client.calls if call[0] == "POST"]


def test_pull_request_failure_reports_the_branch_that_was_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commit is already on the remote; a silent failure makes retries lie."""
    # Arrange
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    fake = _FakeClient(
        fail={
            f"/repos/{OWNER}/{REPO}/pulls": GitHubApiError(
                "Validation Failed", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
            )
        }
    )
    monkeypatch.setattr(pr_tool, "GitHubRestClient", lambda _token=None: fake)

    # Act
    result = _call()

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "pr_failed"
    assert result["branch_created"] is True
    assert HEAD_BRANCH in result["error"]


def test_missing_token_fails_before_any_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    for name in ("GITHUB_MCP_AUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    fake = _FakeClient()
    monkeypatch.setattr(pr_tool, "GitHubRestClient", lambda _token=None: fake)

    # Act
    result = _call()

    # Assert
    assert result["ok"] is False
    assert result["error_kind"] == "github_token_missing"
    assert fake.calls == []


def test_available_when_only_an_env_token_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container case: a token in the environment, no verified store entry."""
    # Arrange
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    registered = _registered_tool()

    # Act / Assert
    assert registered.is_available({}) is True


def test_unavailable_without_any_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    for name in ("GITHUB_MCP_AUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    registered = _registered_tool()

    # Act / Assert
    assert registered.is_available({}) is False
