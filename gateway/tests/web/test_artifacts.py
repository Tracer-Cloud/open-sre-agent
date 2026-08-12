"""Unit tests for local→S3 investigation artifacts (no live AWS)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ReadTimeoutError

from gateway.web.artifacts import (
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


def test_upload_bounds_every_s3_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbounded client pins the single worker thread; botocore's defaults allow
    ~10 minutes per request."""
    captured: dict[str, Any] = {}
    fake = MagicMock()

    def _client(_service: str, *, config: Any) -> Any:
        captured["config"] = config
        return fake

    import boto3

    monkeypatch.setenv(ARTIFACTS_BUCKET_ENV, "bucket")
    monkeypatch.setattr(boto3, "client", _client)
    local = write_local_report("inv-1", {}, base_dir=tmp_path)

    upload_report_to_s3(local, org_id="org", investigation_id="inv-1")

    config = captured["config"]
    assert config.connect_timeout is not None and config.read_timeout is not None
    # "adaptive" mode blocks in TokenBucket.acquire() under throttling, which no
    # socket timeout bounds — the point of pinning the mode here.
    assert config.retries["mode"] == "standard"
    worst_case = config.retries["max_attempts"] * (config.connect_timeout + config.read_timeout)
    assert worst_case <= 180, f"worst-case S3 wall clock grew to {worst_case}s"


def test_upload_gives_up_on_timeout_without_failing_the_investigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A timed-out mirror is not an investigation failure — the report is on disk."""

    class _TimingOut:
        def upload_file(self, *_args: object, **_kwargs: object) -> None:
            raise ReadTimeoutError(endpoint_url="https://s3.amazonaws.com/bucket")

    import boto3

    monkeypatch.setenv(ARTIFACTS_BUCKET_ENV, "bucket")
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: _TimingOut())
    local = write_local_report("inv-1", {}, base_dir=tmp_path)

    with caplog.at_level(logging.WARNING, logger="gateway.web.artifacts"):
        key = upload_report_to_s3(local, org_id="org", investigation_id="inv-1")

    assert key is None
    assert "timed out" in caplog.text
