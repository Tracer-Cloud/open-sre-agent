"""Unit tests for local→S3 investigation artifacts (no live AWS)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gateway.http.artifacts import (
    ARTIFACTS_BUCKET_ENV,
    upload_report_to_s3,
    write_local_report,
)


def test_write_local_report_creates_json(tmp_path: Path) -> None:
    path = write_local_report("inv-1", {"report": "ok"}, base_dir=tmp_path)

    assert path == tmp_path / "inv-1" / "report.json"
    assert json.loads(path.read_text()) == {"report": "ok"}


def test_upload_skips_when_bucket_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ARTIFACTS_BUCKET_ENV, raising=False)
    local = write_local_report("inv-1", {}, base_dir=tmp_path)

    assert upload_report_to_s3(local, org_id="org", investigation_id="inv-1") is None


def test_upload_returns_none_on_s3_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def upload_file(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("network")

    import boto3

    monkeypatch.setenv(ARTIFACTS_BUCKET_ENV, "bucket")
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: _Boom())
    local = write_local_report("inv-1", {}, base_dir=tmp_path)

    assert upload_report_to_s3(local, org_id="org", investigation_id="inv-1") is None


def test_upload_uses_no_org_prefix_when_org_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = MagicMock()
    import boto3

    monkeypatch.setenv(ARTIFACTS_BUCKET_ENV, "bucket")
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: fake)
    local = write_local_report("inv-1", {}, base_dir=tmp_path)

    key = upload_report_to_s3(local, org_id="", investigation_id="inv-1")

    assert key == "no-org/inv-1/report.json"
    fake.upload_file.assert_called_once()
