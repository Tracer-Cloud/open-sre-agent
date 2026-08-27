"""Evidence mapper tests for the S3 storage investigation tools."""

from __future__ import annotations

from typing import Any

from integrations.s3.tools.s3_get_object_tool import _map_get_s3_object
from integrations.s3.tools.s3_inspect_tool import _map_inspect_s3_object
from integrations.s3.tools.s3_list_tool import _map_list_s3_objects
from integrations.s3.tools.s3_marker_tool import _map_check_s3_marker


def test_check_s3_marker_mapper_records_present_marker() -> None:
    evidence: dict[str, Any] = {}

    _map_check_s3_marker(
        evidence,
        {"marker_exists": True, "file_count": 3, "files": ["a", "b", "c"]},
        {},
    )

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "check_s3_marker",
            "label": "S3 Success Marker",
            "summary": "marker present, 3 files",
            "url": None,
            "snippet": None,
        }
    ]


def test_check_s3_marker_mapper_records_missing_marker() -> None:
    evidence: dict[str, Any] = {}

    _map_check_s3_marker(
        evidence,
        {"marker_exists": False, "file_count": 0, "files": []},
        {},
    )

    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["summary"] == "marker missing, 0 files"


def test_check_s3_marker_mapper_records_nothing_without_result() -> None:
    evidence: dict[str, Any] = {}

    _map_check_s3_marker(evidence, {}, {})

    assert "catalog_entries" not in evidence


def test_check_s3_marker_mapper_records_nothing_on_listing_error() -> None:
    evidence: dict[str, Any] = {}

    _map_check_s3_marker(
        evidence,
        {"error": "boto3 not available", "bucket": "b", "prefix": "p/"},
        {},
    )

    assert "catalog_entries" not in evidence


def test_get_s3_object_mapper_records_metadata_not_content() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "found": True,
        "bucket": "audit-bucket",
        "key": "runs/1/payload.json",
        "size": 12,
        "content_type": "application/json",
        "content": '{"secret": "do-not-cite"}',
        "metadata": {},
    }

    _map_get_s3_object(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "get_s3_object",
            "label": "S3 Object",
            "summary": "s3://audit-bucket/runs/1/payload.json, 12 bytes, application/json",
            "url": None,
            "snippet": None,
        }
    ]


def test_get_s3_object_mapper_records_nothing_on_miss_or_error() -> None:
    for output in (
        {},
        {"error": "AccessDenied", "bucket": "b", "key": "k"},
        {"found": False, "bucket": "b", "key": "k", "message": "Object does not exist"},
    ):
        evidence: dict[str, Any] = {}
        _map_get_s3_object(evidence, output, {})
        assert "catalog_entries" not in evidence


def test_inspect_s3_object_mapper_records_metadata_not_sample() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "found": True,
        "bucket": "data-bucket",
        "key": "in/file.csv",
        "size": 2048,
        "last_modified": "2026-08-01 12:00:00+00:00",
        "content_type": "text/csv",
        "sample": "id,name\n1,alice",
    }

    _map_inspect_s3_object(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "inspect_s3_object",
            "label": "S3 Object Inspection",
            "summary": (
                "s3://data-bucket/in/file.csv, 2048 bytes, last modified 2026-08-01 12:00:00+00:00"
            ),
            "url": None,
            "snippet": None,
        }
    ]


def test_inspect_s3_object_mapper_records_nothing_on_miss_or_error() -> None:
    for output in (
        {},
        {"error": "NoSuchKey", "bucket": "b", "key": "k"},
        {"found": False, "bucket": "b", "key": "k", "message": "Object does not exist"},
    ):
        evidence: dict[str, Any] = {}
        _map_inspect_s3_object(evidence, output, {})
        assert "catalog_entries" not in evidence


def test_list_s3_objects_mapper_records_entry() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "found": True,
        "bucket": "data-bucket",
        "prefix": "out/",
        "count": 2,
        "objects": [{"key": "out/a.csv"}, {"key": "out/b.csv"}],
        "is_truncated": False,
    }

    _map_list_s3_objects(evidence, output, {})

    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert entries == [
        {
            "source": "list_s3_objects",
            "label": "S3 Objects",
            "summary": "2 objects",
            "url": None,
            "snippet": None,
        }
    ]


def test_list_s3_objects_mapper_notes_truncation() -> None:
    evidence: dict[str, Any] = {}

    _map_list_s3_objects(
        evidence,
        {
            "count": 100,
            "objects": [{"key": "a"}],
            "is_truncated": True,
        },
        {},
    )

    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert entries[0]["summary"] == "100 objects, truncated"


def test_list_s3_objects_mapper_records_nothing_without_objects() -> None:
    for output in (
        {},
        {"error": "AccessDenied", "bucket": "b", "prefix": ""},
        {"found": False, "count": 0, "objects": [], "is_truncated": False},
    ):
        evidence: dict[str, Any] = {}
        _map_list_s3_objects(evidence, output, {})
        assert "catalog_entries" not in evidence
