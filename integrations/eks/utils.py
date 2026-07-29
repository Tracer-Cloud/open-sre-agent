"""EKS helper utilities.

Callers must provide a stored AWS credential dict (snake_case keys). Returns
credentials in the identical structure as botocore Credentials (PascalCase keys).
"""

from __future__ import annotations

from typing import Any


def stored_credentials_to_aws_creds(
    credentials: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize a stored AWS credential dict for use with botocore.

    Takes a dict with ``access_key_id``, ``secret_access_key``, and optionally
    ``session_token``. Returns a new dict with guaranteed truthy ``AccessKeyId``
    and ``SecretAccessKey`` keys, plus ``SessionToken`` (coerced to ``None`` if
    missing/empty). Returns ``None`` if required access/secret keys are falsy
    or missing. The original dictionary is not mutated.
    """
    if not credentials:
        return None
    access_key = credentials.get("access_key_id")
    secret_key = credentials.get("secret_access_key")
    if not access_key or not secret_key:
        return None
    return {
        "AccessKeyId": access_key,
        "SecretAccessKey": secret_key,
        "SessionToken": credentials.get("session_token") or None,
    }
