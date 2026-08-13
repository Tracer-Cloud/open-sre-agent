"""The generic executor must not add query parameters a single-resource read rejects.

Yandex Cloud answers ``GET /compute/v1/instances/{id}?folderId=…`` and the same
path with ``pageSize`` with a bare ``404`` — no message, nothing that names the
offending parameter. Injecting either one unconditionally therefore made every
resource that does exist look missing, and only through this tool: the curated
per-service tools build their own queries and were never affected.
"""

from __future__ import annotations

import pytest

from integrations.yandex_cloud.rest_client import accepts_folder_scope, is_collection_path
from tools.registry import get_registered_tool_map


class _RecordingBackend:
    """Captures what the tool decided to send."""

    def __init__(self) -> None:
        self.query: dict[str, object] = {}
        self.page_token = ""

    def execute_yc_operation(
        self, service: str, path: str, query: dict[str, object], page_token: str
    ) -> dict[str, object]:
        self.query = dict(query)
        self.page_token = page_token
        return {"success": True, "service": service, "path": path, "data": {}}


def _run(path: str) -> dict[str, object]:
    backend = _RecordingBackend()
    tool = get_registered_tool_map()["execute_yc_operation"]
    tool.run(service="compute", path=path, yc_backend=backend, folder_id="b1gtest")
    return backend.query


class TestPathShape:
    @pytest.mark.parametrize(
        "path",
        [
            "/compute/v1/instances",
            "/resource-manager/v1/folders",
            "/managed-postgresql/v1/clusters/c1/hosts",
            "/monitoring/v2/dashboards",
        ],
    )
    def test_collections_are_recognised(self, path: str) -> None:
        assert is_collection_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/compute/v1/instances/abc123",
            "/resource-manager/v1/folders/b1gtest",
            "/managed-postgresql/v1/clusters/c1/hosts/host1",
            "/compute/v1/instances/abc123:serialPortOutput",
        ],
    )
    def test_single_resources_are_recognised(self, path: str) -> None:
        assert is_collection_path(path) is False

    def test_a_path_without_a_version_segment_is_not_a_collection(self) -> None:
        """Guessing wrong here costs a 404; not guessing at all costs nothing."""
        assert is_collection_path("/operations/abc123") is False


class TestWhatTheToolSends:
    def test_a_collection_read_gets_the_folder(self) -> None:
        assert _run("/compute/v1/instances")["folderId"] == "b1gtest"

    def test_a_single_resource_read_gets_neither(self) -> None:
        query = _run("/compute/v1/instances/abc123")

        assert "folderId" not in query
        assert "pageSize" not in query

    def test_an_explicit_folder_still_wins_on_a_collection(self) -> None:
        backend = _RecordingBackend()
        tool = get_registered_tool_map()["execute_yc_operation"]
        tool.run(
            service="compute",
            path="/compute/v1/instances",
            params={"folderId": "b1gother"},
            yc_backend=backend,
            folder_id="b1gtest",
        )

        assert backend.query["folderId"] == "b1gother"


class TestFolderScope:
    def test_resource_manager_is_not_folder_scoped(self) -> None:
        """It manages folders themselves, so one cannot scope it."""
        assert accepts_folder_scope("resource-manager") is False

    def test_ordinary_services_are(self) -> None:
        for service in ("compute", "iam", "container-registry", "mdb-postgresql"):
            assert accepts_folder_scope(service) is True

    def test_no_folder_is_added_to_a_resource_manager_collection(self) -> None:
        backend = _RecordingBackend()
        tool = get_registered_tool_map()["execute_yc_operation"]
        tool.run(
            service="resource-manager",
            path="/resource-manager/v1/folders",
            params={"cloudId": "b1gcloud"},
            yc_backend=backend,
            folder_id="b1gtest",
        )

        assert "folderId" not in backend.query
        assert backend.query["cloudId"] == "b1gcloud"

    def test_a_caller_scoping_by_cloud_is_left_alone(self) -> None:
        """cloudId means the caller already chose a scope, whatever the service."""
        backend = _RecordingBackend()
        tool = get_registered_tool_map()["execute_yc_operation"]
        tool.run(
            service="compute",
            path="/compute/v1/instances",
            params={"cloudId": "b1gcloud"},
            yc_backend=backend,
            folder_id="b1gtest",
        )

        assert "folderId" not in backend.query
