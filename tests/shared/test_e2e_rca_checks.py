from __future__ import annotations

from tests.shared.e2e_rca_checks import (
    audit_key_mentioned,
    investigation_text_blob,
    s3_key_mentioned,
)


def test_investigation_text_blob_decodes_url_encoded_s3_paths() -> None:
    blob = investigation_text_blob(
        {"trace": "prefix=ingested%2F20260515-111004%2Fdata.json"},
    )
    assert "ingested/20260515-111004/data.json" in blob


def test_s3_key_mentioned_accepts_plain_and_encoded_paths() -> None:
    key = "ingested/20260515-111004/data.json"
    assert s3_key_mentioned(key, key)
    assert s3_key_mentioned("prefix=ingested%2f20260515-111004%2fdata.json", key)


def test_audit_key_mentioned_accepts_plain_and_encoded_paths() -> None:
    key = "audit/trigger-20260515-111004.json"
    assert audit_key_mentioned(key, key)
    assert audit_key_mentioned("prefix=audit%2ftrigger-20260515-111004.json", key)


def test_audit_key_mentioned_when_absent() -> None:
    assert audit_key_mentioned("no audit references", "")
