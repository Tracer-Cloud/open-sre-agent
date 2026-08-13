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


class TestApplicationBalancersAreListedNotFabricated:
    """Application target health follows a nested path this tool does not walk.

    The network balancer's ``:getTargetStates`` action does not exist for the
    application balancer, whose target states live under
    ``/loadBalancers/{id}/targetStates/{backend_group}/{target_group}``. So the
    tool lists application balancers with their status and points at the real
    path rather than calling a URL the API does not serve.
    """

    def test_an_application_balancer_is_listed_with_a_pointer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            called.append(url)
            if "/apploadbalancer/v1/loadBalancers" in url:
                return httpx.Response(
                    200,
                    json={
                        "loadBalancers": [{"id": "alb-1", "name": "ingress", "status": "ACTIVE"}]
                    },
                )
            return httpx.Response(200, json={"loadBalancers": []})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_lb_health(type="application", **_CREDENTIALS)

        alb = result["balancers"][0]
        assert alb["status"] == "ACTIVE"
        assert "targetStates" in alb["target_health"]
        # The tool must not invent per-target health by hitting a path the API
        # does not serve for application balancers.
        assert not any(":getTargetStates" in url for url in called)
        assert result["unhealthy_targets"] == []
