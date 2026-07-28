"""Unit test for CloudTrail's ``_shape_event`` normalizer against a saved payload.

Loads a realistic raw CloudTrail event fixture and calls ``_shape_event``
directly — no AWS SDK call, no mocked HTTP client, no tool invocation.
"""

from __future__ import annotations

import json
from pathlib import Path

from integrations.cloudtrail.tools.cloudtrail_events_tool import _shape_event

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "cloudtrail" / "single_lookup_event.json"
)


def test_shape_event_normalizes_raw_fixture() -> None:
    raw = json.loads(_FIXTURE.read_text())

    shaped = _shape_event(raw)

    assert shaped["event_id"] == "3a7c9e1b-5f2d-4a8e-b1c6-9d4e7a2f8c15"
    assert shaped["event_name"] == "DeleteBucketPolicy"
    assert shaped["event_source"] == "s3.amazonaws.com"
    assert shaped["username"] == "release-bot"
    # ReadOnly arrives as the string "false" and must be coerced to a real bool.
    assert shaped["read_only"] is False
    # High-signal fields are pulled out of the CloudTrailEvent JSON string.
    assert shaped["aws_region"] == "eu-west-1"
    assert shaped["source_ip_address"] == "203.0.113.55"
    assert shaped["error_code"] == "AccessDenied"
    # Only the valid dict entry survives; the null entry marks the list truncated.
    assert shaped["resources"] == [{"type": "AWS::S3::Bucket", "name": "prod-artifacts-bucket"}]
    assert shaped["resources_truncated"] is True
