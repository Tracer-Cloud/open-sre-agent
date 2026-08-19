"""Yandex Compute instance tools.

What these tools are for is narrowing: turning "the service is broken" into a
named host that is actually unhealthy. So the assertions here care less about
field plumbing than about whether the unhealthy thing is picked out of the
healthy ones, and whether the response says what to do next.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.tools import get_yc_instance_diagnostics, list_yc_instances

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


class TestCompute:
    def test_stopped_instances_are_picked_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {"id": "a", "name": "web-1", "status": "RUNNING"},
                            {"id": "b", "name": "web-2", "status": "STOPPED"},
                        ]
                    }
                }
            ),
        )
        result = list_yc_instances(**_CREDENTIALS)

        assert result["count"] == 2
        assert [item["name"] for item in result["unhealthy"]] == ["web-2"]

    def test_addresses_are_surfaced_for_matching_against_an_alert(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {
                                "id": "a",
                                "name": "web-1",
                                "status": "RUNNING",
                                "networkInterfaces": [
                                    {
                                        "primaryV4Address": {
                                            "address": "10.0.0.5",
                                            "oneToOneNat": {"address": "51.2.3.4"},
                                        }
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
        )
        instance = list_yc_instances(**_CREDENTIALS)["instances"][0]

        assert instance["private_addresses"] == ["10.0.0.5"]
        assert instance["public_addresses"] == ["51.2.3.4"]

    def test_name_filter_narrows_the_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {"id": "a", "name": "web-1", "status": "RUNNING"},
                            {"id": "b", "name": "db-1", "status": "RUNNING"},
                        ]
                    }
                }
            ),
        )
        result = list_yc_instances(name_filter="web", **_CREDENTIALS)

        assert result["count"] == 1

    def test_serial_console_is_read_for_diagnosis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The console is where an unreachable VM still reports what is wrong."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    ":serialPortOutput": {"contents": "kernel: Out of memory"},
                    "/compute/v1/instances/fhm1": {"id": "fhm1", "status": "RUNNING"},
                }
            ),
        )
        result = get_yc_instance_diagnostics(instance_id="fhm1", **_CREDENTIALS)

        assert "Out of memory" in result["serial_port_output"]

    def test_an_unreadable_console_does_not_fail_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if ":serialPortOutput" in url:
                return httpx.Response(
                    HTTPStatus.BAD_REQUEST, json={"message": "instance is stopped"}
                )
            return httpx.Response(HTTPStatus.OK, json={"id": "fhm1", "status": "STOPPED"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = get_yc_instance_diagnostics(instance_id="fhm1", **_CREDENTIALS)

        assert result["available"] is True
        assert "stopped" in result["serial_port_error"]

    def test_a_missing_instance_id_asks_for_one(self) -> None:
        result = get_yc_instance_diagnostics(instance_id="", **_CREDENTIALS)

        assert result["available"] is False
        assert "list_yc_instances" in result["error"]


class TestAFilteredPageSaysSoWhenMorePagesExist:
    """Yandex filters names by equality only, so the fragment match is local.

    An empty result then means "not on this page", which reads as "no such
    instance" unless the tool says otherwise.
    """

    def test_a_filtered_read_with_more_pages_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [
                            {"id": "a", "name": "db-1", "status": "RUNNING"},
                        ],
                        "nextPageToken": "page-2",
                    }
                }
            ),
        )

        result = list_yc_instances(name_filter="web", **_CREDENTIALS)

        assert result["count"] == 0
        assert "page_token" in result["note"]

    def test_a_filtered_read_on_the_last_page_is_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/compute/v1/instances": {
                        "instances": [{"id": "a", "name": "db-1", "status": "RUNNING"}]
                    }
                }
            ),
        )

        result = list_yc_instances(name_filter="web", **_CREDENTIALS)

        assert "note" not in result
