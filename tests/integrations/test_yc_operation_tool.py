"""The generic Yandex Cloud read tools.

``execute_yc_operation`` is the one tool that can reach any service, so its
guardrails carry more weight than usual: the host is only ever looked up from
the endpoint registry, the path is checked before anything is sent, and only
GET is available — which across Yandex's uniformly generated REST API is
exactly the set of list and get endpoints.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.tools import execute_yc_operation
from tools.registry import get_registered_tool_map

FOLDER = "b1gexamplefolder"

_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.yandex_cloud.endpoints._fetch_endpoints", dict)
    from integrations.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


class _Backend:
    """Stands in for the synthetic fixture backend."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str]] = []

    def execute_yc_operation(
        self, service: str, path: str, params: dict[str, Any], page_token: str
    ) -> dict[str, Any]:
        self.calls.append((service, path, params, page_token))
        return {"success": True, "service": service, "path": path, "data": {"stub": True}}

    def known_endpoints(self) -> dict[str, str]:
        return {"compute": "compute.fixture.local"}


class TestRegistration:
    def test_both_tools_are_discoverable(self) -> None:
        """Tools stay invisible until their package is listed for discovery."""
        registered = get_registered_tool_map("action")

        assert "execute_yc_operation" in registered
        assert "find_yc_api" in registered

    def test_credentials_are_hidden_from_the_model(self) -> None:
        tool = get_registered_tool_map("action")["execute_yc_operation"]
        properties = set(tool.public_input_schema.get("properties", {}))

        assert properties == {"service", "path", "params", "page_token"}
        for secret in ("sa_key", "oauth_token", "iam_token"):
            assert secret not in properties


class TestGuardrails:
    def test_an_unknown_service_is_refused_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never(*_a: Any, **_k: Any) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _never)
        result = execute_yc_operation(service="not-a-service", path="/v1/x", **_CREDENTIALS)

        assert result["success"] is False
        assert "Unknown Yandex Cloud service" in result["error"]

    @pytest.mark.parametrize(
        "path",
        [
            "compute/v1/instances",
            "/compute/../../../etc/passwd",
            "https://evil.example/steal",
        ],
    )
    def test_a_suspicious_path_is_refused(self, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
        def _never(*_a: Any, **_k: Any) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _never)
        result = execute_yc_operation(service="compute", path=path, **_CREDENTIALS)

        assert result["success"] is False
        assert "Invalid path" in result["error"]

    def test_reads_are_issued_as_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The read-only guarantee rests on this: Yandex mutates over POST/PATCH/DELETE."""
        seen: list[str] = []

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            seen.append(method)
            return httpx.Response(HTTPStatus.OK, json={})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        execute_yc_operation(service="compute", path="/compute/v1/instances", **_CREDENTIALS)

        assert seen == ["GET"]

    def test_missing_credentials_report_unavailable(self) -> None:
        result = execute_yc_operation(service="compute", path="/compute/v1/instances")

        assert result.get("available") is False


class TestReads:
    def test_the_configured_folder_is_used_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured.update(kwargs["params"])
            return httpx.Response(HTTPStatus.OK, json={"instances": []})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        execute_yc_operation(service="compute", path="/compute/v1/instances", **_CREDENTIALS)

        assert captured["folderId"] == FOLDER

    def test_an_explicit_folder_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured.update(kwargs["params"])
            return httpx.Response(HTTPStatus.OK, json={})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        execute_yc_operation(
            service="compute",
            path="/compute/v1/instances",
            params={"folderId": "b1other"},
            **_CREDENTIALS,
        )

        assert captured["folderId"] == "b1other"

    def test_the_folder_is_taken_from_the_instance_when_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On a VM the folder is not in the credentials at all — it is discovered.

        Reading it from the raw credentials sends no folderId, and Yandex
        rejects most list endpoints outright with a validation error.
        """
        from integrations.yandex_cloud import metadata

        monkeypatch.setattr(metadata, "fetch_folder_id", lambda: "b1gfrominstance")
        monkeypatch.setattr(
            metadata,
            "fetch_token",
            lambda: metadata.MetadataToken(token="t1.from-instance", ttl_seconds=600),
        )
        captured: dict[str, Any] = {}

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            captured.update(kwargs["params"])
            return httpx.Response(HTTPStatus.OK, json={})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = execute_yc_operation(
            service="compute",
            path="/compute/v1/instances",
            folder_id="",
            use_metadata=True,
        )

        assert result["success"] is True
        assert captured["folderId"] == "b1gfrominstance"

    def test_the_next_page_token_is_surfaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(HTTPStatus.OK, json={"nextPageToken": "page-2"}),
        )
        result = execute_yc_operation(
            service="compute", path="/compute/v1/instances", **_CREDENTIALS
        )

        assert result["metadata"]["next_page_token"] == "page-2"

    def test_long_responses_are_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(
                200, json={"instances": [{"id": str(n)} for n in range(150)]}
            ),
        )
        result = execute_yc_operation(
            service="compute", path="/compute/v1/instances", **_CREDENTIALS
        )

        assert "more items truncated" in result["data"]["instances"][-1]


class TestEndpointRegistry:
    def test_log_reading_is_listed_separately_from_management(self) -> None:
        """Reading log entries goes to a different host, and sending them to the
        management host returns 415 with no hint about why."""
        from integrations.yandex_cloud.endpoints import known_endpoints

        endpoints = known_endpoints()

        assert endpoints["log-reading"] == "reader.logging.yandexcloud.net"
        assert endpoints["logging"] == "logging.api.cloud.yandex.net"


class TestSyntheticBackend:
    def test_reads_go_to_the_backend_when_one_is_attached(self) -> None:
        backend = _Backend()

        result = execute_yc_operation(
            service="compute",
            path="/compute/v1/instances",
            yc_backend=backend,
            **_CREDENTIALS,
        )

        assert result["data"] == {"stub": True}
        assert backend.calls[0][0] == "compute"
        assert backend.calls[0][2]["folderId"] == FOLDER


class TestItNamesOnlyToolsThatExist:
    """Naming a tool the tree does not ship hands the model a dead next step.

    SKILL.md had the same class of bug and was fixed; the tool description and
    the client's error text were missed because a documentation sweep does not
    reach string literals in code.
    """

    def test_no_shipped_text_names_a_missing_tool(self) -> None:
        from tools.registry import get_registered_tool_map

        registered = set(get_registered_tool_map("action"))
        tool = get_registered_tool_map()["execute_yc_operation"]
        named = {
            word.strip(".,'\"")
            for word in tool.description.split()
            if word.strip(".,'\"").startswith(
                ("list_yc", "find_yc", "get_yc", "read_yc", "query_yc")
            )
        }

        assert named, "the description should point at a discovery tool"
        assert named <= registered, f"names tools that do not exist: {named - registered}"


class TestTheIndexIsTheAllowlist:
    def test_a_path_the_index_does_not_list_never_hits_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _never(*_a: object, **_k: object) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _never)
        result = execute_yc_operation(
            service="compute",
            path="/compute/v1/notARealCollection",
            **_CREDENTIALS,
        )

        assert result["success"] is False
        assert "find_yc_api" in result["error"]
        assert "not a documented read" in result["error"]

    def test_the_gate_holds_for_a_name_only_the_tools_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The load-balancer tool says "alb"; the index says "apploadbalancer"."""

        def _never(*_a: object, **_k: object) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _never)
        result = execute_yc_operation(
            service="alb",
            path="/apploadbalancer/v1/notARealCollection",
            **_CREDENTIALS,
        )

        assert result["success"] is False
        assert "not a documented read" in result["error"]

    @pytest.mark.parametrize("service", ["COMPUTE", "Compute", " compute "])
    def test_the_gate_holds_however_the_service_is_spelled(
        self, service: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Host resolution case-folds the service, so the allowlist must too.

        Otherwise the same string reads as an unknown service to the gate - which
        skips the check - and as a real host to the client, and an undocumented
        path reaches the network.
        """

        def _never(*_a: object, **_k: object) -> None:
            raise AssertionError("no request should be made")

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _never)
        result = execute_yc_operation(
            service=service,
            path="/compute/v1/notARealCollection",
            **_CREDENTIALS,
        )

        assert result["success"] is False
        assert "not a documented read" in result["error"]

    def test_a_concrete_resource_path_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        def _request(method: str, url: str, **_kwargs: object) -> httpx.Response:
            seen.append(url)
            return httpx.Response(HTTPStatus.OK, json={"id": "abc"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = execute_yc_operation(
            service="compute",
            path="/compute/v1/instances/abc",
            **_CREDENTIALS,
        )

        assert result["success"] is True
        assert seen and "instances/abc" in seen[0]
