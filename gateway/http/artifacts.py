"""Investigation report artifacts: local file first, S3 upload when configured."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR

ARTIFACTS_BUCKET_ENV = "OPENSRE_ARTIFACTS_BUCKET"
_DEFAULT_ARTIFACTS_DIR = OPENSRE_HOME_DIR / "investigations"

logger = logging.getLogger(__name__)


def write_local_report(
    investigation_id: str,
    result: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> Path:
    """Write the investigation result to local disk and return its path."""
    path = (base_dir or _DEFAULT_ARTIFACTS_DIR) / investigation_id / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, default=str, indent=2))
    return path


def upload_report_to_s3(
    local_path: Path,
    *,
    org_id: str,
    investigation_id: str,
) -> str | None:
    """Upload the local report to S3 and return its key; None when unconfigured or failed."""
    bucket = os.getenv(ARTIFACTS_BUCKET_ENV, "").strip()
    if not bucket:
        return None
    key = f"{org_id or 'no-org'}/{investigation_id}/report.json"
    try:
        import boto3

        boto3.client("s3").upload_file(str(local_path), bucket, key)
    except Exception:
        logger.warning("[investigations] S3 upload failed for %s", investigation_id, exc_info=True)
        return None
    return key
