"""Shared RCA success-signal helpers for live upstream E2E tests."""

from __future__ import annotations

import json
from urllib.parse import unquote


def investigation_text_blob(result: object) -> str:
    """Lowercased investigation JSON, including URL-decoded paths for matching."""
    raw = json.dumps(result).lower()
    return unquote(raw)


def _path_variants(path: str) -> tuple[str, ...]:
    lowered = path.lower()
    encoded = lowered.replace("/", "%2f")
    return lowered, encoded


def s3_key_mentioned(investigation_text: str, s3_key: str) -> bool:
    """True when the investigation references the landing object or ingested prefix."""
    if s3_key:
        return any(variant in investigation_text for variant in _path_variants(s3_key))
    return "ingested/" in investigation_text or "ingested%2f" in investigation_text


def audit_key_mentioned(investigation_text: str, audit_key: str) -> bool:
    """True when the investigation references the audit artifact or audit prefix."""
    if not audit_key.strip():
        return True
    if any(variant in investigation_text for variant in _path_variants(audit_key)):
        return True
    return "audit/" in investigation_text or "audit%2f" in investigation_text
