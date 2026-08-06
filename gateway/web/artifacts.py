"""Investigation report artifacts: local file first, S3 upload when configured."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

from config.constants import OPENSRE_HOME_DIR

ARTIFACTS_BUCKET_ENV = "OPENSRE_ARTIFACTS_BUCKET"
_DEFAULT_ARTIFACTS_DIR = OPENSRE_HOME_DIR / "investigations"

# Bounds every S3 request to custom parameters;
# Retry mode is "standard"
_S3_CONNECT_TIMEOUT_SECONDS = 5
_S3_READ_TIMEOUT_SECONDS = 30
_S3_MAX_ATTEMPTS = 3

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


# In practice, Reports stay under S3's 8MB multipart threshold, so upload_file()
# uses single PutObject (~2 min bound above applies). If multipart triggers,
# each part is still bounded the same way -- worker unblocks eventually, just
# slower (parts x ~2 min), not unbounded.
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
        from botocore.config import Config

        client = boto3.client(
            "s3",
            config=Config(
                connect_timeout=_S3_CONNECT_TIMEOUT_SECONDS,
                read_timeout=_S3_READ_TIMEOUT_SECONDS,
                retries={"max_attempts": _S3_MAX_ATTEMPTS, "mode": "standard"},
            ),
        )
        client.upload_file(str(local_path), bucket, key)
    except (ConnectTimeoutError, ReadTimeoutError):
        # Non-fatal: the report is already on local disk.
        logger.warning(
            "[investigations] S3 upload timed out for %s after %ss connect / %ss read "
            "(%s attempts); local report at %s is unaffected",
            investigation_id,
            _S3_CONNECT_TIMEOUT_SECONDS,
            _S3_READ_TIMEOUT_SECONDS,
            _S3_MAX_ATTEMPTS,
            local_path,
        )
        return None
    except Exception:
        logger.warning("[investigations] S3 upload failed for %s", investigation_id, exc_info=True)
        return None
    return key
