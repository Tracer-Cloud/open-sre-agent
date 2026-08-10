"""Cloud Audit Log filter construction.

Audit entries live in ordinary Cloud Logging, distinguished only by log name,
so this builds the ``logName`` clause plus the ``protoPayload`` predicates that
turn "show me the logs" into "show me who deleted this". Assembling the filter
from named fields rather than taking a raw expression is the point of the tool:
the audit schema is the part operators consistently get wrong, and a
hand-written filter that names ``jsonPayload`` instead of ``protoPayload``
returns nothing with no indication why.

Log-name matching uses the ``:`` (has) operator rather than ``=``. Equality
requires the fully qualified ``projects/<id>/logs/...`` name, which cannot be
written once for a query spanning several projects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from integrations.gcp.tools.gcp_logging_query_tool.filters import clamp_hours

#: Audit log streams, keyed by the value the tool accepts. ``activity`` is the
#: default because admin writes — the mutations that cause incidents — land
#: there, and it is on by default in every project. ``data_access`` is off by
#: default and usually empty unless someone enabled it.
LOG_TYPES: dict[str, str] = {
    "activity": "cloudaudit.googleapis.com%2Factivity",
    "data_access": "cloudaudit.googleapis.com%2Fdata_access",
    "system_event": "cloudaudit.googleapis.com%2Fsystem_event",
    "policy": "cloudaudit.googleapis.com%2Fpolicy",
    "all": "cloudaudit.googleapis.com",
}

DEFAULT_LOG_TYPE = "activity"


def normalize_log_type(log_type: str) -> str:
    """Return a known log-type key, falling back to the default.

    An unrecognised value widens to admin activity rather than failing: the
    caller still gets the stream that answers most questions, instead of an
    error it has to recover from mid-investigation.
    """
    candidate = (log_type or "").strip().lower()
    return candidate if candidate in LOG_TYPES else DEFAULT_LOG_TYPE


def quote(value: str) -> str:
    """Render ``value`` as a Cloud Logging string literal.

    Backslashes and quotes are escaped so a resource name containing either
    cannot terminate the literal early and change the filter's meaning.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_audit_filter(
    *,
    log_type: str = DEFAULT_LOG_TYPE,
    principal: str = "",
    method: str = "",
    service: str = "",
    resource: str = "",
    failed_only: bool = False,
    hours: float = 24.0,
    now: datetime | None = None,
) -> str:
    """Compose the Cloud Logging filter for an audit query.

    The time bound is always applied — see
    :func:`integrations.gcp.tools.gcp_logging_query_tool.filters.build_filter`
    for why an unbounded audit query is worse than useless on a busy project.
    """
    moment = now or datetime.now(UTC)
    start = moment - timedelta(hours=clamp_hours(hours))
    clauses = [
        f'timestamp >= "{start.strftime("%Y-%m-%dT%H:%M:%SZ")}"',
        f"logName:{quote(LOG_TYPES[normalize_log_type(log_type)])}",
    ]

    if principal.strip():
        # Substring, not equality: the agent often has a service-account short
        # name or a user's local part, not the full principal email.
        clauses.append(f"protoPayload.authenticationInfo.principalEmail:{quote(principal.strip())}")
    if method.strip():
        clauses.append(f"protoPayload.methodName:{quote(method.strip())}")
    if service.strip():
        clauses.append(f"protoPayload.serviceName:{quote(service.strip())}")
    if resource.strip():
        clauses.append(f"protoPayload.resourceName:{quote(resource.strip())}")
    if failed_only:
        # A successful audit entry omits status.code entirely rather than
        # setting it to zero, so "failed" is "code is present and non-zero".
        clauses.append("protoPayload.status.code != 0")

    return " AND ".join(clauses)
