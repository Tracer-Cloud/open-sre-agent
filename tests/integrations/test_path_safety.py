"""Tests for integrations.path_safety -- the shared HTTP path-segment guard."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from integrations.jira.client import JiraClient, make_jira_client
from integrations.path_safety import safe_path_segment
from integrations.rootly.client import RootlyClient, make_rootly_client

_TOKEN = "test-token"
_BASE_URL = "https://api.example.com"


class TestSafePathSegment:
    def test_real_id_shapes_round_trip_unchanged(self) -> None:
        """The over-validation guard. safe_path_segment(x) == x for real ids."""
        real_ids = [
            "10001",  # bare numeric id
            "ALT-7",  # uppercase prefix
            "PIJ1234",  # alphanumeric
            "PROJ-123",  # project-style
            "alert-1",  # lowercase with dash
            "dpl_abc-123",  # mixed separators
            "proj-123",  # lowercase project key
            "550e8400-e29b-41d4-a716-446655440000",  # UUID
        ]

        for real_id in real_ids:
            assert safe_path_segment(real_id) == real_id

    def test_structural_rejections(self) -> None:
        """Table over structural rejections -- all return None."""
        rejections = [
            "",  # empty
            "  ",  # whitespace-only
            "..",  # traversal
            "../x",  # traversal with suffix
            "a/../b",  # embedded traversal
            "a//b",  # double slash
            "a b",  # space
            "a%2Fb",  # percent-encoded slash
            "a:b",  # colon
            "a?x=1",  # query
            "a#f",  # fragment
            "a" * 257,  # over length limit
        ]

        for bad_input in rejections:
            assert safe_path_segment(bad_input) is None


def _rootly_client_over_transport(handler: Any) -> RootlyClient:
    """A real RootlyClient whose only fake part is the network."""
    client = make_rootly_client(_TOKEN, base_url=_BASE_URL)
    assert client is not None
    client._client = httpx.Client(
        base_url=client.config.base_url,
        headers=client.config.headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def _jira_client_over_transport(handler: Any, monkeypatch: pytest.MonkeyPatch) -> JiraClient:
    """A real JiraClient whose only fake part is the network.

    ``JiraClient`` has no ``_client`` attribute -- it builds a fresh
    ``httpx.Client`` per call in ``_get_client()`` -- so that factory is the
    only seam that makes a leaked URL observable.
    """
    client = make_jira_client(_BASE_URL, "test@example.com", _TOKEN, "TEST")
    assert client is not None

    def build_client() -> httpx.Client:
        return httpx.Client(base_url=_BASE_URL, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(client, "_get_client", build_client)
    return client


def _record_requests() -> tuple[Any, list[httpx.Request]]:
    """Return (handler, requests_list) where handler records to the list."""
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={})

    return handler, recorded


class _CaptureServiceErrorRecorder:
    """Named class for recording capture_service_error calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


def _capture_service_error_recorder() -> _CaptureServiceErrorRecorder:
    """Return a named callable for recording capture_service_error calls."""
    return _CaptureServiceErrorRecorder()


# Note: sorted tuples to ensure deterministic test collection for xdist
CLIENT_METHODS_ROOTLY = [
    ("get_alert", "alert_id", {}),
    ("get_incident", "incident_id", {}),
    ("list_incident_events", "incident_id", {}),
    ("post_timeline_event", "incident_id", {"event": "test event"}),
]

CLIENT_METHODS_JIRA = [
    ("add_comment", "issue_key", {"body": "test body"}),
    ("get_issue", "issue_key", {}),
    ("update_issue", "issue_key", {"fields": {"summary": "test"}}),
]


class TestClientPathSafety:
    @pytest.mark.parametrize("method_name,kwarg_name,extra_kwargs", CLIENT_METHODS_ROOTLY)
    def test_rootly_absolute_url_id_never_leaves_the_configured_host(
        self, method_name: str, kwarg_name: str, extra_kwargs: dict[str, Any]
    ) -> None:
        """The must-exist one. Hostile absolute URL in id gets rejected."""
        handler, recorded = _record_requests()
        client = _rootly_client_over_transport(handler)
        method = getattr(client, method_name)

        # Call with hostile absolute URL
        kwargs: dict[str, Any] = {
            kwarg_name: "https://attacker.invalid/v1/alerts/1",
            **extra_kwargs,
        }
        result = method(**kwargs)

        # Must reject without making any request
        assert len(recorded) == 0
        assert result["success"] is False

        # Positive control - verify httpx would actually redirect
        test_client = httpx.Client(base_url=_BASE_URL)
        request = test_client.build_request("GET", "https://attacker.invalid/x")
        assert request.url.host == "attacker.invalid"

    @pytest.mark.parametrize("method_name,kwarg_name,extra_kwargs", CLIENT_METHODS_JIRA)
    def test_jira_absolute_url_id_never_leaves_the_configured_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
        method_name: str,
        kwarg_name: str,
        extra_kwargs: dict[str, Any],
    ) -> None:
        """The must-exist one. Hostile absolute URL in id gets rejected."""
        handler, recorded = _record_requests()
        client = _jira_client_over_transport(handler, monkeypatch)
        method = getattr(client, method_name)

        # Call with hostile absolute URL
        kwargs: dict[str, Any] = {kwarg_name: "https://attacker.invalid/v1/issue/1", **extra_kwargs}
        result = method(**kwargs)

        # Must reject without making any request
        assert len(recorded) == 0
        assert result["success"] is False

    @pytest.mark.parametrize("method_name,kwarg_name,extra_kwargs", CLIENT_METHODS_ROOTLY)
    def test_rootly_traversal_id_does_not_reach_a_sibling_endpoint(
        self, method_name: str, kwarg_name: str, extra_kwargs: dict[str, Any]
    ) -> None:
        """Traversal id gets rejected without making requests."""
        handler, recorded = _record_requests()
        client = _rootly_client_over_transport(handler)
        method = getattr(client, method_name)

        kwargs: dict[str, Any] = {kwarg_name: "../../v1/users", **extra_kwargs}
        result = method(**kwargs)

        # Must reject without making any request
        assert len(recorded) == 0
        assert result["success"] is False

        # Positive control - verify httpx would actually resolve traversal
        test_client = httpx.Client(base_url=_BASE_URL)
        request = test_client.build_request("GET", "../../v1/users")
        assert str(request.url).endswith("/v1/users")

    @pytest.mark.parametrize("method_name,kwarg_name,extra_kwargs", CLIENT_METHODS_JIRA)
    def test_jira_traversal_id_does_not_reach_a_sibling_endpoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
        method_name: str,
        kwarg_name: str,
        extra_kwargs: dict[str, Any],
    ) -> None:
        """Traversal id gets rejected without making requests."""
        handler, recorded = _record_requests()
        client = _jira_client_over_transport(handler, monkeypatch)
        method = getattr(client, method_name)

        kwargs: dict[str, Any] = {kwarg_name: "../../rest/api/2/user", **extra_kwargs}
        result = method(**kwargs)

        # Must reject without making any request
        assert len(recorded) == 0
        assert result["success"] is False

    def test_rejection_files_no_telemetry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rejected ids don't trigger capture_service_error."""
        recorder = _capture_service_error_recorder()
        monkeypatch.setattr("integrations.rootly.client.capture_service_error", recorder)

        client = _rootly_client_over_transport(lambda _: httpx.Response(200, json={}))
        result = client.get_incident("../../v1/users")

        assert result["success"] is False
        assert len(recorder.calls) == 0

    def test_legitimate_id_produces_the_expected_outbound_url(self) -> None:
        """End to end: real id produces the expected URL and round-trips byte-identical."""
        recorded: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # Record the request and return realistic data
            recorded.append(request)
            return httpx.Response(200, json={"data": {"id": "alert-1", "status": "triggered"}})

        client = _rootly_client_over_transport(handler)

        result = client.get_alert("alert-1")

        assert len(recorded) == 1
        assert recorded[0].url.raw_path == b"/v1/alerts/alert-1"
        assert result["success"] is True

    def test_legitimate_jira_key_reaches_the_intended_issue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Jira half of the over-validation guard: a real key is untouched."""
        recorded: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            recorded.append(request)
            return httpx.Response(200, json={"key": "PROJ-123", "fields": {}})

        client = _jira_client_over_transport(handler, monkeypatch)

        result = client.get_issue("PROJ-123")

        assert len(recorded) == 1
        assert recorded[0].url.raw_path.endswith(b"/issue/PROJ-123")
        assert result["success"] is True
