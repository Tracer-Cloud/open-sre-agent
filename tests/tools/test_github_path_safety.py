"""Tests for GitHub path safety -- owner/repo/ref validation and same-origin checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock
from urllib.request import Request

import pytest

from integrations.github.branches import branch_exists
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.path_safety import repo_path, safe_git_ref, safe_repo
from integrations.github.tools.work_status import list_github_security_alerts

_GITHUB_PACKAGE = Path(__file__).resolve().parents[2] / "integrations" / "github"

# Any f-string that interpolates straight into a /repos/ path bypasses repo_path.
_UNGUARDED_REPO_PATH_RE = re.compile(r'f"[^"]*/?repos/\{')


def _recording_urlopen(
    recorded: list[Request], body: bytes = b"{}", headers: dict[str, str] | None = None
) -> Any:
    """Return a named fake ``urlopen`` that records every Request it is handed."""

    def fake_urlopen(req: Request, *, timeout: int = 20) -> MagicMock:  # noqa: ARG001
        recorded.append(req)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body
        response.headers = headers or {}
        return response

    return fake_urlopen


class TestGitHubOutboundPaths:
    """The over-validation regressions: a guard that mangles a working path is an outage."""

    def test_branch_with_slash_reaches_the_unencoded_ref_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``feat/foo`` must arrive as ``heads/feat/foo``, not ``heads/feat%2Ffoo``.

        Encoding the separator turns every ref lookup for a normal branch name
        into a 404, which reads as "the branch does not exist".
        """
        recorded: list[Request] = []
        monkeypatch.setattr(
            "integrations.github.client.request.urlopen",
            _recording_urlopen(recorded, body=b'{"object": {"sha": "abc"}}'),
        )

        assert branch_exists(
            GitHubRestClient("test-token"), owner="owner", repo="repo", branch="feat/foo"
        )

        assert len(recorded) == 1
        assert recorded[0].selector == "/repos/owner/repo/git/ref/heads/feat/foo"

    def test_security_alert_endpoints_keep_their_separator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``dependabot/alerts`` is two path segments, not one encoded segment."""
        recorded: list[Request] = []
        monkeypatch.setattr(
            "integrations.github.client.request.urlopen",
            _recording_urlopen(recorded, body=b"[]"),
        )

        list_github_security_alerts(
            owner="owner", repo="repo", alert_type="dependabot", github_token="test-token"
        )

        assert len(recorded) == 1
        assert recorded[0].selector.startswith("/repos/owner/repo/dependabot/alerts?")

    def test_no_unguarded_repo_path_interpolation_remains(self) -> None:
        """Every ``/repos/`` path in the package is built through ``repo_path``."""
        offenders = [
            f"{source.relative_to(_GITHUB_PACKAGE)}:{lineno}"
            for source in sorted(_GITHUB_PACKAGE.rglob("*.py"))
            if source.name != "path_safety.py"
            for lineno, line in enumerate(source.read_text().splitlines(), start=1)
            if _UNGUARDED_REPO_PATH_RE.search(line)
        ]
        assert offenders == []


class TestGitHubPathValidation:
    def test_hash_truncation_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An id containing # must be rejected before any request."""
        recorded_requests: list[Request] = []

        def fake_urlopen(req: Request, *, timeout: int = 20) -> Mock:  # noqa: ARG001
            recorded_requests.append(req)
            response = Mock()
            response.read.return_value = b'{"default_branch": "main"}'
            response.headers = {}
            return response

        monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)

        client = GitHubRestClient("test-token")

        # This should be rejected by repo_path before any request
        with pytest.raises(GitHubApiError):
            client.request("GET", repo_path("owner", "repo#fragment", "issues"))

        # No requests should have been made
        assert len(recorded_requests) == 0

    def test_dot_dot_in_owner_repo_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """.. in an owner/repo is rejected before any request."""
        recorded_requests: list[Request] = []

        def fake_urlopen(req: Request, *, timeout: int = 20) -> Mock:  # noqa: ARG001
            recorded_requests.append(req)
            response = Mock()
            response.read.return_value = b'{"default_branch": "main"}'
            response.headers = {}
            return response

        monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)

        client = GitHubRestClient("test-token")

        # This should be rejected by safe_owner before any request
        with pytest.raises(GitHubApiError):
            client.request("GET", repo_path("../malicious", "repo", "issues"))

        assert len(recorded_requests) == 0

    def test_dotgithub_repo_accepted_and_round_trips(self) -> None:
        """.github as a repo name is accepted and round-trips."""
        assert safe_repo(".github") == ".github"

        # Verify repo_path doesn't reject it
        path = repo_path("owner", ".github")
        assert path == "/repos/owner/.github"

    def test_branch_with_slash_accepted_and_slash_survives(self) -> None:
        """A branch with / (feat/foo) is accepted and / survives quote."""
        ref = "feat/foo"
        safe_ref = safe_git_ref(ref)
        assert safe_ref == "feat/foo"  # / should be preserved

    def test_percent_encoded_traversal_in_ref_rejected(self) -> None:
        """%2e%2e%2f in a ref is rejected."""
        malicious_ref = "feat%2e%2e%2fmalicious"
        assert safe_git_ref(malicious_ref) is None

    def test_paginate_stops_on_cross_origin_link_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """paginate stops on a cross-origin Link header and the token is not sent off-host."""
        requests_made: list[Request] = []

        def fake_urlopen(req: Request, *, timeout: int = 20) -> Mock:  # noqa: ARG001
            requests_made.append(req)
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            if len(requests_made) == 1:
                # First response with cross-origin Link header
                response.read.return_value = b'[{"id": 1}]'
                response.headers = {"Link": '<https://attacker.invalid/evil>; rel="next"'}
            else:
                # This should never be reached
                response.read.return_value = b'[{"id": 2}]'
                response.headers = {}
            return response

        monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)

        client = GitHubRestClient("test-token", base_url="https://api.github.com")
        result = client.paginate("/repos/owner/repo/issues")

        # Should have made exactly one request (stopped due to cross-origin)
        assert len(requests_made) == 1
        assert requests_made[0].full_url == "https://api.github.com/repos/owner/repo/issues"

        # Should have returned the first page only
        assert result == [{"id": 1}]

    def test_hash_truncation_locally_provable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """# in a path truncates the path client-side in urllib - locally provable."""
        recorded_requests: list[Request] = []

        def fake_urlopen(req: Request, *, timeout: int = 20) -> Mock:  # noqa: ARG001
            recorded_requests.append(req)
            response = Mock()
            response.read.return_value = b'{"default_branch": "main"}'
            response.headers = {}
            return response

        monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)

        client = GitHubRestClient("test-token")

        # If not for the path safety check, this would be truncated by urllib
        with pytest.raises(GitHubApiError):
            client.request("GET", repo_path("owner", "repo", "issues", "123#fragment"))

        # Positive control: verify urllib would actually truncate
        from urllib.request import Request as UrllibRequest

        req = UrllibRequest("https://api.github.com/repos/owner/repo/issues/123#fragment")
        assert req.selector == "/repos/owner/repo/issues/123"  # truncated at #
