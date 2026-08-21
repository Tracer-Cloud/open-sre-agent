"""Yandex load balancer health tools."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.tools import get_yc_lb_health

FOLDER = "b1gexamplefolder"
_CREDENTIALS: dict[str, Any] = {"folder_id": FOLDER, "iam_token": "t1.token"}


@pytest.fixture(autouse=True)
def _no_endpoint_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.yandex_cloud.endpoints._fetch_endpoints", dict)
    from integrations.yandex_cloud.endpoints import reset_endpoint_cache

    reset_endpoint_cache()


def _responder(routes: dict[str, dict[str, Any]]) -> Any:
    """Return an httpx.request stand-in that matches on a path fragment."""

    def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
        for fragment, payload in routes.items():
            if fragment in url:
                return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

    return _request


class TestLoadBalancers:
    def test_unhealthy_targets_are_collected_across_balancers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Partial target health is what explains intermittent errors."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    ":getTargetStates": {
                        "targetStates": [
                            {"address": "10.0.0.1", "status": "HEALTHY"},
                            {"address": "10.0.0.2", "status": "UNHEALTHY"},
                        ]
                    },
                    "/load-balancer/v1/networkLoadBalancers": {
                        "loadBalancers": [
                            {
                                "id": "nlb-1",
                                "name": "edge",
                                "status": "ACTIVE",
                                "attachedTargetGroups": [{"targetGroupId": "tg-1"}],
                            }
                        ]
                    },
                    "/apploadbalancer/v1/loadBalancers": {"loadBalancers": []},
                }
            ),
        )
        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["count"] == 1
        assert len(result["unhealthy_targets"]) == 1
        assert result["unhealthy_targets"][0]["address"] == "10.0.0.2"
        assert result["unhealthy_targets"][0]["balancer"] == "edge"

    def test_every_attached_target_group_is_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A second attached group must not be skipped — it can hold the only failure."""

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            params = kwargs.get("params") or {}
            if "/load-balancer/v1/networkLoadBalancers" in url and ":getTargetStates" not in url:
                return httpx.Response(
                    HTTPStatus.OK,
                    json={
                        "loadBalancers": [
                            {
                                "id": "nlb-1",
                                "name": "edge",
                                "status": "ACTIVE",
                                "attachedTargetGroups": [
                                    {"targetGroupId": "tg-healthy"},
                                    {"targetGroupId": "tg-sick"},
                                ],
                            }
                        ]
                    },
                )
            if ":getTargetStates" in url:
                group = str(params.get("targetGroupId", ""))
                status = "HEALTHY" if group == "tg-healthy" else "UNHEALTHY"
                return httpx.Response(
                    HTTPStatus.OK,
                    json={
                        "targetStates": [
                            {
                                "address": f"10.0.0.{2 if status == 'UNHEALTHY' else 1}",
                                "status": status,
                            }
                        ]
                    },
                )
            if "/apploadbalancer/v1/loadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": url})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)

        result = get_yc_lb_health(**_CREDENTIALS)

        assert [t["address"] for t in result["unhealthy_targets"]] == ["10.0.0.2"]
        assert result["unhealthy_targets"][0]["target_group"] == "tg-sick"

    def test_network_target_state_read_sends_no_page_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Yandex answers a target-state read that carries a stray pageSize with a bare 404."""
        state_queries: list[dict[str, Any]] = []

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            params = dict(kwargs.get("params") or {})
            if ":getTargetStates" in url:
                state_queries.append(params)
                return httpx.Response(HTTPStatus.OK, json={"targetStates": []})
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(
                    HTTPStatus.OK,
                    json={
                        "loadBalancers": [
                            {
                                "id": "nlb-1",
                                "name": "edge",
                                "status": "ACTIVE",
                                "attachedTargetGroups": [{"targetGroupId": "tg-1"}],
                            }
                        ]
                    },
                )
            return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)

        get_yc_lb_health(**_CREDENTIALS)

        assert state_queries == [{"targetGroupId": "tg-1"}]

    def test_one_balancer_type_can_be_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            seen.append(url)
            return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        get_yc_lb_health(type="application", **_CREDENTIALS)

        assert all("apploadbalancer" in url for url in seen)


class TestAnIncompleteListSaysSo:
    """ "No unhealthy targets" must never be the answer to a truncated read."""

    def test_a_further_page_is_reported_rather_than_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/load-balancer/v1/networkLoadBalancers": {
                        "loadBalancers": [
                            {"id": "nlb-1", "name": "edge", "status": "ACTIVE"},
                        ],
                        "nextPageToken": "page-2",
                    },
                    "/apploadbalancer/v1/loadBalancers": {"loadBalancers": []},
                }
            ),
        )

        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["complete"] is False
        assert "execute_yc_operation" in result["note"]

    def test_a_single_page_is_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/load-balancer/v1/networkLoadBalancers": {"loadBalancers": []},
                    "/apploadbalancer/v1/loadBalancers": {"loadBalancers": []},
                }
            ),
        )

        result = get_yc_lb_health(**_CREDENTIALS)

        assert "complete" not in result
        assert "note" not in result


# Response shapes captured live from a real application balancer (folder
# b1g...i3): listener -> router -> route -> backend group -> target group ->
# targetStates, where health is reported per zone.
_ALB_ROUTES = {
    "/apploadbalancer/v1/loadBalancers/alb-1/targetStates/bg-1/tg-1": {
        "targetStates": [
            {
                "target": {"subnetId": "sn-a", "ipAddress": "10.0.0.5"},
                "status": {"zoneStatuses": [{"zoneId": "ru-central1-a", "status": "UNHEALTHY"}]},
            },
            {
                "target": {"subnetId": "sn-b", "ipAddress": "10.0.0.6"},
                "status": {
                    "zoneStatuses": [
                        {"zoneId": "ru-central1-a", "status": "UNHEALTHY"},
                        {"zoneId": "ru-central1-b", "status": "HEALTHY"},
                    ]
                },
            },
        ]
    },
    "/apploadbalancer/v1/backendGroups/bg-1": {
        "http": {"backends": [{"targetGroups": {"targetGroupIds": ["tg-1"]}}]},
        "id": "bg-1",
    },
    "/apploadbalancer/v1/httpRouters/router-1/virtualHosts": {
        "virtualHosts": [{"routes": [{"http": {"route": {"backendGroupId": "bg-1"}}}]}]
    },
    "/apploadbalancer/v1/loadBalancers": {
        "loadBalancers": [
            {
                "id": "alb-1",
                "name": "ingress",
                "status": "ACTIVE",
                "listeners": [{"http": {"handler": {"httpRouterId": "router-1"}}}],
            }
        ]
    },
}


class TestApplicationTargetsReachAggregation:
    """A failing application backend must surface in unhealthy_targets.

    The tool walks the balancer's backend graph to its targetStates, so an
    unhealthy application target feeds the same summary the network balancer
    does. A target is unhealthy only when every zone fails its health check.
    """

    def test_an_unhealthy_application_target_is_collected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        unhealthy = result["unhealthy_targets"]
        assert len(unhealthy) == 1
        assert unhealthy[0]["address"] == "10.0.0.5"
        assert unhealthy[0]["balancer"] == "ingress"
        # A target healthy in any zone is not reported unhealthy.
        assert all(t["address"] != "10.0.0.6" for t in unhealthy)

    def test_an_unnamed_balancer_is_labelled_by_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A real application balancer can come back without a name; use the id."""
        routes = {k: v for k, v in _ALB_ROUTES.items() if k != "/apploadbalancer/v1/loadBalancers"}
        routes["/apploadbalancer/v1/loadBalancers"] = {
            "loadBalancers": [
                {
                    "id": "alb-1",
                    "status": "ACTIVE",
                    "listeners": [{"http": {"handler": {"httpRouterId": "router-1"}}}],
                }
            ]
        }

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            for fragment, payload in routes.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["unhealthy_targets"][0]["balancer"] == "alb-1"

    def test_a_grpc_route_backend_is_walked_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A route is HTTP or gRPC; reading only HTTP hides gRPC targets."""
        routes = dict(_ALB_ROUTES)
        routes["/apploadbalancer/v1/httpRouters/router-1/virtualHosts"] = {
            "virtualHosts": [{"routes": [{"grpc": {"route": {"backendGroupId": "bg-1"}}}]}]
        }

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            for fragment, payload in routes.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        assert any(t["address"] == "10.0.0.5" for t in result["unhealthy_targets"])


class TestAFailedGraphReadIsNotSilentHealth:
    """An unreadable balancer graph must not read as "nothing unhealthy".

    The walk crosses three reads - virtual hosts, backend group, target states -
    and any of them can fail. Treating a failure as an empty collection produces
    an empty unhealthy_targets that looks like a clean bill of health.
    """

    def _responder_failing_at(self, failing_fragment: str) -> Any:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            if failing_fragment in url:
                return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, json={"message": "boom"})
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        return _request

    @pytest.mark.parametrize(
        "failing",
        [
            "/virtualHosts",
            "/apploadbalancer/v1/backendGroups/bg-1",
            "/targetStates/bg-1/tg-1",
        ],
    )
    def test_a_failed_read_marks_the_result_incomplete(
        self, monkeypatch: pytest.MonkeyPatch, failing: str
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            self._responder_failing_at(failing),
        )

        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["unhealthy_targets"] == []
        assert result["complete"] is False
        assert "not evidence" in result["note"]

    def test_a_fully_readable_graph_is_not_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The warning must not fire when every read succeeded."""

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        assert result["unhealthy_targets"]
        assert "complete" not in result


class TestVirtualHostPagingIsFollowed:
    """A backend group reachable only from a later page must still be walked.

    Reading one page and reporting complete is the same silent-truncation bug
    as the balancer list had, one level deeper in the graph.
    """

    def test_a_second_page_of_virtual_hosts_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen_tokens: list[str] = []

        def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            if "/virtualHosts" in url:
                token = (kwargs.get("params") or {}).get("pageToken", "")
                seen_tokens.append(token)
                if not token:
                    # first page points at a backend group with no targets
                    return httpx.Response(
                        200,
                        json={
                            "virtualHosts": [
                                {"routes": [{"http": {"route": {"backendGroupId": "bg-empty"}}}]}
                            ],
                            "nextPageToken": "page-2",
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "virtualHosts": [
                            {"routes": [{"http": {"route": {"backendGroupId": "bg-1"}}}]}
                        ]
                    },
                )
            if "/backendGroups/bg-empty" in url:
                return httpx.Response(HTTPStatus.OK, json={"id": "bg-empty"})
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        assert "page-2" in seen_tokens, "the second page was never requested"
        # the unhealthy target lives behind the backend group on page two
        assert any(t["address"] == "10.0.0.5" for t in result["unhealthy_targets"])


class TestEveryListenerShapeIsWalked:
    """Listener shapes other than plain HTTP must not hide their backends.

    Shapes taken from the API reference: http.handler.httpRouterId,
    tls.defaultHandler.{httpHandler,streamHandler}, tls.sniHandlers[].handler.*,
    and stream.handler.backendGroupId. HTTP handlers name a router; stream
    handlers name a backend group directly and so never appear behind one.
    """

    def _walk(self, monkeypatch: pytest.MonkeyPatch, listener: dict[str, Any]) -> dict[str, Any]:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            if "/apploadbalancer/v1/loadBalancers" in url and "targetStates" not in url:
                return httpx.Response(
                    200,
                    json={
                        "loadBalancers": [
                            {
                                "id": "alb-1",
                                "name": "ingress",
                                "status": "ACTIVE",
                                "listeners": [listener],
                            }
                        ]
                    },
                )
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url and "loadBalancers" not in fragment:
                    return httpx.Response(HTTPStatus.OK, json=payload)
            if "targetStates" in url:
                return httpx.Response(
                    200,
                    json=_ALB_ROUTES[
                        "/apploadbalancer/v1/loadBalancers/alb-1/targetStates/bg-1/tg-1"
                    ],
                )
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        return get_yc_lb_health(**_CREDENTIALS)

    @pytest.mark.parametrize(
        ("shape", "listener"),
        [
            (
                "tls sni http handler",
                {
                    "tls": {
                        "sniHandlers": [{"handler": {"httpHandler": {"httpRouterId": "router-1"}}}]
                    }
                },
            ),
            (
                "tls default http handler",
                {"tls": {"defaultHandler": {"httpHandler": {"httpRouterId": "router-1"}}}},
            ),
        ],
    )
    def test_a_router_behind_a_tls_handler_is_followed(
        self, monkeypatch: pytest.MonkeyPatch, shape: str, listener: dict[str, Any]
    ) -> None:
        result = self._walk(monkeypatch, listener)

        assert any(t["address"] == "10.0.0.5" for t in result["unhealthy_targets"]), shape

    @pytest.mark.parametrize(
        ("shape", "listener"),
        [
            ("stream listener", {"stream": {"handler": {"backendGroupId": "bg-1"}}}),
            (
                "tls default stream handler",
                {"tls": {"defaultHandler": {"streamHandler": {"backendGroupId": "bg-1"}}}},
            ),
            (
                "tls sni stream handler",
                {
                    "tls": {
                        "sniHandlers": [{"handler": {"streamHandler": {"backendGroupId": "bg-1"}}}]
                    }
                },
            ),
        ],
    )
    def test_a_backend_group_named_on_the_listener_is_read(
        self, monkeypatch: pytest.MonkeyPatch, shape: str, listener: dict[str, Any]
    ) -> None:
        """Stream handlers bypass the router entirely."""
        result = self._walk(monkeypatch, listener)

        assert any(t["address"] == "10.0.0.5" for t in result["unhealthy_targets"]), shape


class TestOneBackendInTwoTargetGroups:
    """The same IP can serve several target groups and fail in only one.

    Collapsing records by address alone let a healthy reading from the first
    group hide a failing one from the second, which is the difference between
    "this backend is fine" and "this backend is failing for one route".
    """

    def test_a_failure_in_the_second_group_is_not_hidden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shared = "10.0.0.7"

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "/load-balancer/v1/networkLoadBalancers" in url:
                return httpx.Response(HTTPStatus.OK, json={"loadBalancers": []})
            if "targetStates/bg-1/tg-healthy" in url:
                return httpx.Response(
                    200,
                    json={
                        "targetStates": [
                            {
                                "target": {"subnetId": "sn-a", "ipAddress": shared},
                                "status": {"zoneStatuses": [{"status": "HEALTHY"}]},
                            }
                        ]
                    },
                )
            if "targetStates/bg-1/tg-failing" in url:
                return httpx.Response(
                    200,
                    json={
                        "targetStates": [
                            {
                                "target": {"subnetId": "sn-a", "ipAddress": shared},
                                "status": {"zoneStatuses": [{"status": "UNHEALTHY"}]},
                            }
                        ]
                    },
                )
            if "/apploadbalancer/v1/backendGroups/bg-1" in url:
                return httpx.Response(
                    200,
                    json={
                        "id": "bg-1",
                        "http": {
                            "backends": [
                                {"targetGroups": {"targetGroupIds": ["tg-healthy", "tg-failing"]}}
                            ]
                        },
                    },
                )
            if "/virtualHosts" in url:
                return httpx.Response(
                    200,
                    json={
                        "virtualHosts": [
                            {"routes": [{"http": {"route": {"backendGroupId": "bg-1"}}}]}
                        ]
                    },
                )
            if "/apploadbalancer/v1/loadBalancers" in url:
                return httpx.Response(
                    200,
                    json={
                        "loadBalancers": [
                            {
                                "id": "alb-1",
                                "name": "ingress",
                                "status": "ACTIVE",
                                "listeners": [{"http": {"handler": {"httpRouterId": "router-1"}}}],
                            }
                        ]
                    },
                )
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        failing = [t for t in result["unhealthy_targets"] if t["address"] == shared]
        assert len(failing) == 1, "the unhealthy relationship was collapsed away"
        assert failing[0]["target_group"] == "tg-failing"
