"""Build an alert from captured failure context and invoke the OpenSRE
investigation pipeline.

Sits between :mod:`use_case` (which captures the failure) and the
scenario tests (which assert on the investigation output). Mirrors the
``trigger-real-failure → invoke-investigation → assert-RCA-quality``
pattern used by :mod:`tests.e2e.upstream_lambda` and
:mod:`tests.e2e.upstream_prefect_ecs_fargate`, including
``create_alert`` for the alert payload and ``@traceable`` for LangSmith
metadata.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langsmith import traceable

from app.cli.investigation import run_investigation_cli
from tests.e2e.openclaw.infrastructure_sdk.local import OpenClawHandle
from tests.utils.alert_factory import create_alert

_PIPELINE_NAME = "openclaw_mcp_bridge"
_ALERT_NAME = "OpenClaw MCP integration unreachable"

# Keys that carry the RCA's narrative text in the result dict returned
# by ``run_investigation_cli``. ``report`` is the rendered slack-style
# report — it contains the diagnosis AND the "## Recommended Actions"
# section, so a single concatenation is enough to substring-check both
# the failure naming and the remediation hints.
_SUMMARY_KEYS: tuple[str, ...] = ("root_cause", "problem_md", "report")


def summarize_result(result: dict[str, Any]) -> str:
    """Lowercase concatenation of the RCA's narrative fields.

    Tests assert on substring presence ("openclaw", "gateway", "stdio",
    etc.) so we join ``root_cause`` + ``problem_md`` + ``report`` once
    and let the test case do simple ``in`` checks. ``report`` already
    embeds the recommended-actions section, so callers don't need a
    separate remediation accessor.
    """
    return " ".join(str(result.get(key, "")) for key in _SUMMARY_KEYS).lower()


def _build_annotations(failure_context: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    """Compose the alert annotations that ground the agent's diagnosis.

    ``context_sources="openclaw"`` is the routing hint that tells the
    pipeline to prefer OpenClaw-aware tools and prompt fragments. The
    failure-mode keys carry the captured details so the agent can
    reason about the specific OpenClaw failure rather than inventing a
    plausible-sounding cause.
    """
    return {
        "context_sources": "openclaw",
        "service_name": "openclaw",
        "correlation_id": correlation_id,
        "failure_mode": failure_context.get("failure_mode", "unknown"),
        "transport_mode": failure_context.get("transport_mode", ""),
        "command": failure_context.get("command", ""),
        # Gateway-down has no URL; expose ``args`` as a fallback so the
        # annotation key is always present for the agent to read.
        "url": failure_context.get("gateway_url") or failure_context.get("args", ""),
        "last_error": failure_context.get("last_error", ""),
        "error_detail": failure_context.get("error_detail", ""),
    }


def run_openclaw_investigation(
    handle: OpenClawHandle,
    failure_context: dict[str, Any],
) -> dict[str, Any]:
    """Build an alert from ``failure_context`` and run the OpenSRE pipeline.

    Wraps :func:`run_investigation_cli` inside a ``@traceable`` block so
    the run shows up in LangSmith with metadata that identifies it as
    an OpenClaw e2e run (handle PIDs, failure mode, correlation id).

    Returns the final agent state dict the scenario test then asserts
    on. Expected keys include ``root_cause``, ``problem_md``,
    ``remediation_steps``, and ``validity_score``.
    """
    failure_mode = failure_context.get("failure_mode", "unknown")
    correlation_id = f"openclaw-e2e-{failure_mode}-{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(UTC).isoformat()
    run_name = f"openclaw_e2e_{failure_mode}_{int(datetime.now(UTC).timestamp())}"

    raw_alert = create_alert(
        pipeline_name=_PIPELINE_NAME,
        run_name=run_name,
        status="failed",
        timestamp=timestamp,
        severity="critical",
        alert_name=_ALERT_NAME,
        environment="local-e2e",
        annotations=_build_annotations(failure_context, correlation_id),
    )

    @traceable(
        run_type="chain",
        name=f"openclaw_e2e_{failure_mode}",
        metadata={
            "alert_id": raw_alert.get("alert_id"),
            "alert_name": _ALERT_NAME,
            "pipeline_name": _PIPELINE_NAME,
            "context_sources": "openclaw",
            "failure_mode": failure_mode,
            "transport_mode": failure_context.get("transport_mode", ""),
            "gateway_pid": handle.gateway_pid,
            "gateway_url": handle.gateway_url,
            "correlation_id": correlation_id,
        },
    )
    def _invoke() -> dict[str, Any]:
        return run_investigation_cli(
            alert_name=_ALERT_NAME,
            pipeline_name=_PIPELINE_NAME,
            severity="critical",
            raw_alert=raw_alert,
        )

    return _invoke()
