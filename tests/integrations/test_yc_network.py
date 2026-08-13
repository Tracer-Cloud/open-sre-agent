"""Yandex load balancer health tools."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from integrations.yc_network.tools import get_yc_lb_health

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
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"message": f"no stub for {url}"})

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

    def test_one_balancer_type_can_be_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            seen.append(url)
            return httpx.Response(200, json={"loadBalancers": []})

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
                return httpx.Response(200, json={"loadBalancers": []})
            for fragment, payload in _ALB_ROUTES.items():
                if fragment in url:
                    return httpx.Response(200, json=payload)
            return httpx.Response(404, json={"message": f"no stub for {url}"})

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
                return httpx.Response(200, json={"loadBalancers": []})
            for fragment, payload in routes.items():
                if fragment in url:
                    return httpx.Response(200, json=payload)
            return httpx.Response(404, json={"message": f"no stub for {url}"})

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
                return httpx.Response(200, json={"loadBalancers": []})
            for fragment, payload in routes.items():
                if fragment in url:
                    return httpx.Response(200, json=payload)
            return httpx.Response(404, json={"message": f"no stub for {url}"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(**_CREDENTIALS)

        assert any(t["address"] == "10.0.0.5" for t in result["unhealthy_targets"])
