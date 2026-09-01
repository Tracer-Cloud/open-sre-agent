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

from integrations.yandex_cloud.mdb_catalog import ENGINE_KEYS, resolve_engine
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
        from integrations.yandex_cloud.mdb_catalog import ENGINES

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


class TestEngineNamesTheCatalogActuallyHas:
    """The engine table has to match Yandex's service list, not last year's."""

    def test_sharded_postgresql_is_its_own_engine(self) -> None:
        """SPQR is a separate service with its own API prefix, not a PostgreSQL mode."""
        engine = resolve_engine("spqr")

        assert engine is not None
        assert engine.service == "managed-spqr"
        assert engine.path == "/managed-spqr/v1"
        assert "ROUTER" in engine.log_service_types

    @pytest.mark.parametrize("name", ["spqr", "sharded postgresql", "sharded-postgresql"])
    def test_the_names_people_type_for_it_resolve(self, name: str) -> None:
        engine = resolve_engine(name)

        assert engine is not None
        assert engine.key == "spqr"

    def test_every_engine_is_listed_once(self) -> None:
        assert len(ENGINE_KEYS) == len(set(ENGINE_KEYS))


class TestHostsLiveWhereTheEngineKeepsThem:
    """Greenplum has no ``hosts`` collection, and the cost of assuming it does is silent."""

    def _greenplum(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/master-hosts": {
                        "hosts": [
                            {
                                "name": "rc1a-master.mdb",
                                "role": "MASTER",
                                "health": "ALIVE",
                                "zoneId": "ru-central1-a",
                            }
                        ]
                    },
                    "/segment-hosts": {
                        "hosts": [
                            {
                                "name": "rc1b-seg1.mdb",
                                "role": "SEGMENT",
                                "health": "DEAD",
                                "zoneId": "ru-central1-b",
                            }
                        ]
                    },
                    "/operations": {"operations": []},
                    "/managed-greenplum/v1/clusters/c1": {
                        "id": "c1",
                        "status": "RUNNING",
                        "health": "ALIVE",
                    },
                }
            ),
        )
        return get_yc_db_cluster(cluster_id="c1", engine="greenplum", **_CREDENTIALS)

    def test_both_host_collections_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._greenplum(monkeypatch)

        assert [host["name"] for host in result["hosts"]] == ["rc1a-master.mdb", "rc1b-seg1.mdb"]

    def test_a_sick_segment_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A segment is where the work happens, so a dead one is what hangs a query."""
        result = self._greenplum(monkeypatch)

        assert [host["name"] for host in result["unhealthy_hosts"]] == ["rc1b-seg1.mdb"]

    def test_the_master_is_what_the_connection_hint_points_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._greenplum(monkeypatch)

        assert result["connect"]["host"] == "rc1a-master.mdb"

    def test_a_failed_host_read_says_so_instead_of_reading_as_no_hosts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty list and a failed read lead to opposite conclusions."""

        def _hosts_are_down(_method: str, url: str, **_kwargs: Any) -> httpx.Response:
            if url.endswith("/hosts"):
                return httpx.Response(
                    HTTPStatus.SERVICE_UNAVAILABLE, json={"message": "temporarily unavailable"}
                )
            if "/operations" in url:
                return httpx.Response(HTTPStatus.OK, json={"operations": []})
            return httpx.Response(HTTPStatus.OK, json={"id": "c1", "status": "RUNNING"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _hosts_are_down)
        result = get_yc_db_cluster(cluster_id="c1", engine="postgresql", **_CREDENTIALS)

        assert result["hosts"] == []
        assert "hosts" in result["hosts_error"]


class TestTheConnectionHintNamesWhichPortItMeans:
    """Several engines listen on two ports, and the wrong one looks like a dead host."""

    def _hint(self, monkeypatch: pytest.MonkeyPatch, engine: str, prefix: str) -> dict[str, Any]:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [{"name": "rc1a.mdb", "role": "MASTER", "health": "ALIVE"}]
                    },
                    "/operations": {"operations": []},
                    f"{prefix}/clusters/c1": {"id": "c1", "status": "RUNNING"},
                }
            ),
        )
        return get_yc_db_cluster(cluster_id="c1", engine=engine, **_CREDENTIALS)["connect"]

    @pytest.mark.parametrize(
        ("engine", "prefix", "tls", "plaintext"),
        [
            ("valkey", "/managed-redis/v1", 6380, 6379),
            ("storedoc", "/managed-mongodb/v1", 27018, 27017),
            ("clickhouse", "/managed-clickhouse/v1", 8443, 8123),
        ],
    )
    def test_both_ports_are_offered_when_they_differ(
        self,
        engine: str,
        prefix: str,
        tls: int,
        plaintext: int,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        connect = self._hint(monkeypatch, engine, prefix)

        assert connect["port"] == tls
        assert connect["port_is_tls"] is True
        assert connect["port_without_tls"] == plaintext

    def test_an_engine_with_one_port_offers_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PostgreSQL answers on 6432 either way, so a second number would be noise."""
        connect = self._hint(monkeypatch, "postgresql", "/managed-postgresql/v1")

        assert connect["port"] == 6432
        assert "port_without_tls" not in connect


class TestStoppedIsNotTheSameAsBroken:
    """A cluster somebody switched off is not an incident, and saying so matters."""

    def _listed(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        payload = {
            "clusters": [
                {"id": "c1", "name": "live", "status": "RUNNING", "health": "ALIVE"},
                {"id": "c2", "name": "switched-off", "status": "STOPPED", "health": ""},
                {"id": "c3", "name": "degraded", "status": "RUNNING", "health": "DEGRADED"},
            ]
        }
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder({"/managed-postgresql/v1/clusters": payload}),
        )
        return list_yc_db_clusters(engine="postgresql", **_CREDENTIALS)

    def test_only_the_broken_one_is_unhealthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._listed(monkeypatch)

        assert [c["name"] for c in result["unhealthy"]] == ["degraded"]

    def test_the_stopped_one_is_still_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hiding it would be worse: "unreachable" is often exactly the answer."""
        result = self._listed(monkeypatch)

        assert [c["name"] for c in result["stopped"]] == ["switched-off"]


class TestHostRolesSurviveEngineDisagreement:
    """Engines name the role field differently, and Greenplum does not have one."""

    def _greenplum_hosts(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        # Shapes taken from a live cluster: health and zoneId are there, role is not.
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/master-hosts": {
                        "hosts": [
                            {
                                "name": "rc1b-master.mdb",
                                "health": "ALIVE",
                                "zoneId": "ru-central1-b",
                            }
                        ]
                    },
                    "/segment-hosts": {
                        "hosts": [
                            {"name": "rc1b-seg1.mdb", "health": "ALIVE", "zoneId": "ru-central1-b"},
                            {"name": "rc1b-seg2.mdb", "health": "ALIVE", "zoneId": "ru-central1-b"},
                        ]
                    },
                    "/operations": {"operations": []},
                    "/managed-greenplum/v1/clusters/c1": {"id": "c1", "status": "RUNNING"},
                }
            ),
        )
        return get_yc_db_cluster(cluster_id="c1", engine="greenplum", **_CREDENTIALS)

    def test_the_collection_names_the_role_when_the_payload_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._greenplum_hosts(monkeypatch)

        assert [host["role"] for host in result["hosts"]] == ["MASTER", "SEGMENT", "SEGMENT"]

    def test_the_hint_points_at_the_master_on_purpose(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not by ordering luck: a failed master-hosts read must not promote a segment."""
        result = self._greenplum_hosts(monkeypatch)

        assert result["connect"]["host"] == "rc1b-master.mdb"

    def test_a_mongo_shaped_type_field_is_used_as_the_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            _responder(
                {
                    "/hosts": {
                        "hosts": [{"name": "rc1b.mdb", "type": "MONGOD", "health": "ALIVE"}]
                    },
                    "/operations": {"operations": []},
                    "/managed-mongodb/v1/clusters/c1": {"id": "c1", "status": "RUNNING"},
                }
            ),
        )
        result = get_yc_db_cluster(cluster_id="c1", engine="storedoc", **_CREDENTIALS)

        assert result["hosts"][0]["role"] == "MONGOD"


class TestNothingIsDroppedOffTheEndOfAPage:
    """Yandex answers a hundred at a time, and a quietly short list is the worst kind."""

    def _paged(self, pages: dict[str, dict[str, Any]]) -> Any:
        """Return a stand-in that serves pages keyed by the token asked for."""

        def _request(_method: str, url: str, **kwargs: Any) -> httpx.Response:
            if "/clusters/" in url and "/hosts" not in url:
                return httpx.Response(HTTPStatus.OK, json={"id": "c1", "status": "RUNNING"})
            if "/operations" in url:
                return httpx.Response(HTTPStatus.OK, json={"operations": []})
            token = str((kwargs.get("params") or {}).get("pageToken", ""))
            return httpx.Response(HTTPStatus.OK, json=pages[token])

        return _request

    def test_a_second_page_of_clusters_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            self._paged(
                {
                    "": {
                        "clusters": [{"id": "c1", "name": "first", "status": "RUNNING"}],
                        "nextPageToken": "page-2",
                    },
                    "page-2": {"clusters": [{"id": "c2", "name": "second", "status": "STOPPED"}]},
                }
            ),
        )

        result = list_yc_db_clusters(engine="postgresql", **_CREDENTIALS)

        assert [c["name"] for c in result["clusters"]] == ["first", "second"]
        assert result["count"] == 2
        assert result["complete"] is True

    def test_the_stopped_cluster_on_the_second_page_is_not_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dropping a page drops findings, not just rows."""
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request",
            self._paged(
                {
                    "": {
                        "clusters": [{"id": "c1", "name": "first", "status": "RUNNING"}],
                        "nextPageToken": "page-2",
                    },
                    "page-2": {"clusters": [{"id": "c2", "name": "second", "status": "STOPPED"}]},
                }
            ),
        )

        result = list_yc_db_clusters(engine="postgresql", **_CREDENTIALS)

        assert [c["name"] for c in result["stopped"]] == ["second"]

    def test_more_pages_than_are_read_are_declared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cap is fine; stopping quietly at it is not."""
        endless = {
            "": {"clusters": [{"id": "c", "name": "n", "status": "RUNNING"}], "nextPageToken": "t"},
            "t": {
                "clusters": [{"id": "c", "name": "n", "status": "RUNNING"}],
                "nextPageToken": "t",
            },
        }
        monkeypatch.setattr(
            "integrations.yandex_cloud.rest_client.send_request", self._paged(endless)
        )

        result = list_yc_db_clusters(engine="postgresql", **_CREDENTIALS)

        assert result["complete"] is False
        assert result["incomplete_engines"] == ["postgresql"]

    def test_a_wide_greenplum_cluster_reads_every_page_of_segments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Greptile did not raise this one, and it is the likelier of the two.

        A hundred segments is an ordinary size for an MPP cluster, and a segment
        missing from the list is a segment nobody checks the health of.
        """

        def _request(_method: str, url: str, **kwargs: Any) -> httpx.Response:
            token = str((kwargs.get("params") or {}).get("pageToken", ""))
            if "segment-hosts" in url:
                if not token:
                    return httpx.Response(
                        HTTPStatus.OK,
                        json={
                            "hosts": [{"name": "seg-1", "health": "ALIVE"}],
                            "nextPageToken": "more",
                        },
                    )
                return httpx.Response(
                    HTTPStatus.OK, json={"hosts": [{"name": "seg-2", "health": "DEAD"}]}
                )
            if "master-hosts" in url:
                return httpx.Response(
                    HTTPStatus.OK, json={"hosts": [{"name": "master-1", "health": "ALIVE"}]}
                )
            if "/operations" in url:
                return httpx.Response(HTTPStatus.OK, json={"operations": []})
            return httpx.Response(HTTPStatus.OK, json={"id": "c1", "status": "RUNNING"})

        monkeypatch.setattr("integrations.yandex_cloud.rest_client.send_request", _request)

        result = get_yc_db_cluster(cluster_id="c1", engine="greenplum", **_CREDENTIALS)

        assert [host["name"] for host in result["hosts"]] == ["master-1", "seg-1", "seg-2"]
        assert [host["name"] for host in result["unhealthy_hosts"]] == ["seg-2"]


class TestTheHandoffToAnOperatorIsInEveryDescription:
    """An investigation that only touched these tools must still be told.

    The SKILL.md slice carrying that instruction attaches to the two generic
    readers, so a database-only investigation never sees it - and ends with
    "increase the timeout" instead of the command that does it.
    """

    @pytest.mark.parametrize(
        "name", ["list_yc_db_clusters", "get_yc_db_cluster", "read_yc_db_logs"]
    )
    def test_the_description_asks_for_the_command(self, name: str) -> None:
        from tools.registry import clear_tool_registry_cache, get_registered_tool_map

        clear_tool_registry_cache()
        description = get_registered_tool_map("investigation")[name].description

        assert "yc ..." in description
        assert "only read" in description

    def test_it_stays_a_sentence_and_not_a_second_copy_of_the_skill(self) -> None:
        """Duplicating the 2400-character slice per tool buys nothing."""
        from integrations.yandex_cloud.tools.yc_db_tool import _READ_ONLY_HANDOFF

        assert len(_READ_ONLY_HANDOFF) < 200
