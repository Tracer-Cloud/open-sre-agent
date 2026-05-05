"""Tests for the integrations credential store."""

from __future__ import annotations

import json
import os
import stat
import threading
from unittest.mock import patch

import pytest

from app.integrations import store as store_mod
from app.integrations.store import _save, upsert_integration


def _assert_private_permissions(store_file) -> None:
    mode = stat.S_IMODE(store_file.stat().st_mode)
    if os.name == "nt":
        # Windows file access is governed by ACLs; chmod-style mode bits are not portable here.
        assert mode & stat.S_IWRITE
        return
    assert mode == 0o600, f"Expected 0o600, got 0o{mode:o}"


class TestSavePermissions:
    def test_saved_file_has_0o600_permissions(self, tmp_path: pytest.TempPathFactory) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        data = {"mariadb": {"host": "db.example.com", "database": "prod"}}

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save(data)

        _assert_private_permissions(store_file)

    def test_saved_file_content_is_valid_json(self, tmp_path: pytest.TempPathFactory) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        data = {"mariadb": {"host": "db.example.com"}}

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save(data)

        content = json.loads(store_file.read_text())
        assert content == data

    def test_save_creates_parent_directories(self, tmp_path: pytest.TempPathFactory) -> None:
        nested = tmp_path / "a" / "b" / "integrations.json"  # type: ignore[operator]

        with patch("app.integrations.store.STORE_PATH", nested):
            _save({"key": "value"})

        assert nested.exists()

    def test_save_overwrites_existing_file_with_correct_permissions(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        store_file.write_text("{}")
        store_file.chmod(0o644)

        with patch("app.integrations.store.STORE_PATH", store_file):
            _save({"updated": True})

        _assert_private_permissions(store_file)
        assert json.loads(store_file.read_text())["updated"] is True


class TestConcurrentWrites:
    """Regression tests for the file-locking fix in #1272.

    Two threads run simultaneous upsert_integration() calls. Without the
    file lock the second writer would overwrite the first writer's record,
    silently dropping it. With the lock both records must survive.
    """

    def test_concurrent_upserts_preserve_all_records(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def write_datadog() -> None:
            try:
                barrier.wait()
                upsert_integration(
                    "datadog", {"credentials": {"api_key": "dd-key", "app_key": "dd-app"}}
                )
            except Exception as exc:
                errors.append(exc)

        def write_grafana() -> None:
            try:
                barrier.wait()
                upsert_integration(
                    "grafana",
                    {"credentials": {"endpoint": "http://grafana:3000", "api_key": "gf-key"}},
                )
            except Exception as exc:
                errors.append(exc)

        with patch.object(store_mod, "STORE_PATH", store_file):
            t1 = threading.Thread(target=write_datadog)
            t2 = threading.Thread(target=write_grafana)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        assert not errors, f"Thread(s) raised: {errors}"

        raw = json.loads(store_file.read_text())
        services = {r["service"] for r in raw["integrations"]}
        assert "datadog" in services, "datadog record was silently dropped"
        assert "grafana" in services, "grafana record was silently dropped"

    def test_concurrent_upserts_produce_valid_json(self, tmp_path: pytest.TempPathFactory) -> None:
        """The store file must always be parseable JSON even under concurrent writes."""
        store_file = tmp_path / "integrations.json"  # type: ignore[operator]
        errors: list[Exception] = []
        services = [f"svc-{i}" for i in range(10)]
        barrier = threading.Barrier(len(services))

        def write_service(name: str) -> None:
            try:
                barrier.wait()
                upsert_integration(name, {"credentials": {"key": name}})
            except Exception as exc:
                errors.append(exc)

        with patch.object(store_mod, "STORE_PATH", store_file):
            threads = [threading.Thread(target=write_service, args=(s,)) for s in services]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Thread(s) raised: {errors}"

        raw_text = store_file.read_text()
        parsed = json.loads(raw_text)  # must not raise
        assert "integrations" in parsed
