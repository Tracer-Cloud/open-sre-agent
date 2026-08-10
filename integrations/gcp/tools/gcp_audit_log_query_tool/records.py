"""Cloud Audit Log entry normalization — the who/what/when of a change.

``AuditLog`` is a deeply nested proto and the generic Cloud Logging
normalization in :mod:`integrations.gcp.tools.gcp_logging_query_tool.entries`
flattens it to a JSON blob, which is correct for arbitrary payloads but wastes
context here: the five fields that answer "who changed what" are known in
advance, so they are lifted to the top level and the rest is dropped.
"""

from __future__ import annotations

from typing import Any

#: User agents from client libraries run to several hundred characters of
#: version metadata. The leading identification is the part that distinguishes
#: Terraform from the console from a stray script.
_MAX_USER_AGENT_CHARS = 120

#: Error messages carry the actionable detail; quota and IAM messages in
#: particular are long but worth most of their length.
_MAX_STATUS_CHARS = 500


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _denied_permissions(payload: dict[str, Any]) -> list[str]:
    """Permissions the caller was checked for and did not have.

    A denied audit entry names the exact missing permission, which converts
    "the deploy failed" into a one-line IAM fix.
    """
    infos = payload.get("authorizationInfo")
    if not isinstance(infos, list):
        return []
    return [
        str(info.get("permission", ""))
        for info in infos
        if isinstance(info, dict) and not info.get("granted") and info.get("permission")
    ]


def _status(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(succeeded, rendered status)``.

    A successful call leaves ``status`` empty or sets ``code`` to zero; only a
    non-zero code means failure.
    """
    status = payload.get("status")
    if not isinstance(status, dict) or not status.get("code"):
        return True, "OK"
    message = _truncate(str(status.get("message", "")).strip(), _MAX_STATUS_CHARS)
    code = status.get("code")
    return False, f"{code}: {message}" if message else f"code {code}"


def normalize_record(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one audit ``LogEntry`` into a single change record."""
    payload = entry.get("protoPayload")
    payload = payload if isinstance(payload, dict) else {}
    auth = payload.get("authenticationInfo")
    auth = auth if isinstance(auth, dict) else {}
    metadata = payload.get("requestMetadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    resource = entry.get("resource")
    resource = resource if isinstance(resource, dict) else {}
    labels = resource.get("labels")
    labels = labels if isinstance(labels, dict) else {}

    succeeded, status = _status(payload)
    record: dict[str, Any] = {
        "timestamp": entry.get("timestamp", ""),
        "severity": entry.get("severity", "DEFAULT"),
        "principal": str(auth.get("principalEmail", "")),
        "method": str(payload.get("methodName", "")),
        "service": str(payload.get("serviceName", "")),
        "resource": str(payload.get("resourceName", "")),
        "resource_type": str(resource.get("type", "")),
        "succeeded": succeeded,
        "status": status,
    }

    project = labels.get("project_id")
    if project:
        record["project_id"] = str(project)
    caller_ip = str(metadata.get("callerIp", "")).strip()
    if caller_ip:
        record["caller_ip"] = caller_ip
    user_agent = str(metadata.get("callerSuppliedUserAgent", "")).strip()
    if user_agent:
        record["user_agent"] = _truncate(user_agent, _MAX_USER_AGENT_CHARS)
    delegate = auth.get("serviceAccountDelegationInfo")
    if isinstance(delegate, list) and delegate:
        # Impersonation: the acting identity is not the one that authenticated,
        # which matters when attributing a change to a human.
        record["delegated"] = True
    denied = _denied_permissions(payload)
    if denied:
        record["denied_permissions"] = denied

    operation = entry.get("operation")
    if isinstance(operation, dict) and operation.get("id"):
        # Long-running operations emit a first and a last entry sharing an id;
        # without it the pair reads as two unrelated changes.
        record["operation_id"] = str(operation.get("id"))
        record["operation_last"] = bool(operation.get("last"))
    return record


def normalize_records(entries: list[Any]) -> list[dict[str, Any]]:
    """Normalize a page of audit entries, skipping anything that is not an object."""
    return [normalize_record(entry) for entry in entries if isinstance(entry, dict)]
