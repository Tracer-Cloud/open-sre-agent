"""Managed database clusters: health, host roles, and what happened recently.

What this tool is for is narrowing - turning "the database is slow" into a named
cluster and a named host that is actually unhealthy, and into the recent
operation that explains it. So the assertions care less about field plumbing
than about whether the unhealthy thing is picked out of the healthy ones, and
whether the answer says what to do next.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.yandex_cloud.mdb_engines import ENGINE_KEYS, resolve_engine
from integrations.yandex_cloud.tools import get_yc_db_cluster, list_yc_db_clusters

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


class TestManagedDatabases:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("postgresql", "postgresql"),
            ("postgres", "postgresql"),
            ("redis", "valkey"),
            ("mongodb", "storedoc"),
            ("greenplum", "greenplum"),
            ("mpp", "greenplum"),
        ],
    )
    def test_former_product_names_still_resolve(self, name: str, expected: str) -> None:
        """Redis became Valkey and MongoDB became StoreDoc; people still say both."""
        engine = resolve_engine(name)

        assert engine is not None
        assert engine.key == expected

    def test_postgresql_uses_the_pooler_port(self) -> None:
        engine = resolve_engine("postgresql")

        assert engine is not None
        assert engine.port == 6432

    def test_every_engine_resolves_to_a_known_endpoint(self) -> None:
        """A key the registry does not carry is refused before the read is sent."""
        from integrations.yandex_cloud.endpoints import STATIC_ENDPOINTS
        from integrations.yandex_cloud.mdb_engines import ENGINES

        for engine in ENGINES:
            assert engine.service in STATIC_ENDPOINTS, engine.key

    def test_engines_answering_with_nothing_is_still_an_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Most folders run one or two engines, so empty is the usual truth."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(HTTPStatus.OK, json={"clusters": []}),
        )
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is True
        assert result["count"] == 0

    def test_no_engine_reachable_is_reported_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            lambda *_a, **_k: httpx.Response(403, json={"message": "permission denied"}),
        )
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is False

    def test_an_unknown_engine_lists_the_valid_ones(self) -> None:
        result = list_yc_db_clusters(engine="oracle", **_CREDENTIALS)

        assert result["available"] is False
        for key in ENGINE_KEYS:
            assert key in result["error"]

    def test_one_engine_failing_does_not_hide_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A folder rarely uses every engine, and permissions are often per-service."""

        def _request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if "managed-postgresql" in url:
                return httpx.Response(
                    200,
                    json={
                        "clusters": [
                            {"id": "c1", "name": "main", "status": "RUNNING", "health": "ALIVE"}
                        ]
                    },
                )
            return httpx.Response(403, json={"message": "permission denied"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)
        result = list_yc_db_clusters(**_CREDENTIALS)

        assert result["available"] is True
        assert result["count"] == 1
        assert "could not be listed" in result["note"]

    def test_recent_operations_expose_a_failover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [
                            {"name": "rc1a.mdb", "role": "MASTER", "health": "ALIVE"},
                            {"name": "rc1b.mdb", "role": "REPLICA", "health": "DEAD"},
                        ]
                    },
                    "/operations": {
                        "operations": [
                            {"id": "op-1", "description": "Failover cluster", "done": True}
                        ]
                    },
                    "/managed-postgresql/v1/clusters/c1": {
                        "id": "c1",
                        "name": "main",
                        "status": "RUNNING",
                        "health": "DEGRADED",
                    },
                }
            ),
        )
        result = get_yc_db_cluster(cluster_id="c1", engine="postgresql", **_CREDENTIALS)

        assert result["recent_operations"][0]["description"] == "Failover cluster"
        assert [host["name"] for host in result["unhealthy_hosts"]] == ["rc1b.mdb"]

    def test_the_response_says_how_to_query_the_data_plane(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [{"name": "rc1a.mdb", "role": "MASTER", "health": "ALIVE"}]
                    },
                    "/operations": {"operations": []},
                    "/managed-postgresql/v1/clusters/c1": {"id": "c1", "status": "RUNNING"},
                }
            ),
        )
        connect = get_yc_db_cluster(cluster_id="c1", engine="postgresql", **_CREDENTIALS)["connect"]

        assert connect["integration"] == "postgresql"
        assert connect["host"] == "rc1a.mdb"
        assert connect["port"] == 6432
        assert "CA.pem" in connect["tls"]
