"""Verification facade: per-service verifiers and the top-level verify_integrations runner.

Verifier callables are sourced from the central plugin registry
(``integrations.verification``). Each service's ``integrations.<service>.verifier``
module is imported lazily, on first lookup via ``get_verifier()`` — not eagerly
for every integration — so this module only pays import costs for services
that are actually checked.
"""

from __future__ import annotations

from typing import Any

from integrations.catalog import (
    resolve_effective_integrations as _resolve_effective_integrations,
)
from integrations.registry import CORE_VERIFY_SERVICES, SUPPORTED_VERIFY_SERVICES
from integrations.slack.verifier import RUNTIME_SEND_TEST_KEY as _SLACK_RUNTIME_SEND_TEST_KEY
from integrations.verification import VerifierFn, get_verifier, result


def resolve_effective_integrations() -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.opensre and environment variables."""
    return _resolve_effective_integrations()


def verify_integrations(
    service: str | None = None,
    *,
    send_slack_test: bool = False,
) -> list[dict[str, str]]:
    """Run verification checks for configured integrations."""
    effective_integrations = resolve_effective_integrations()
    services = [service] if service else list(SUPPORTED_VERIFY_SERVICES)
    results: list[dict[str, str]] = []

    for current_service in services:
        integration = effective_integrations.get(current_service)
        if not integration:
            results.append(
                result(current_service, "-", "missing", "Not configured in local store or env.")
            )
            continue

        verifier = get_verifier(current_service)
        if verifier is None:
            results.append(
                result(
                    current_service,
                    "-",
                    "failed",
                    "Verification is not supported for this service.",
                )
            )
            continue

        config = dict(integration["config"])
        if current_service == "slack" and send_slack_test:
            config[_SLACK_RUNTIME_SEND_TEST_KEY] = True

        try:
            results.append(verifier(str(integration["source"]), config))
        except Exception as exc:
            results.append(
                result(current_service, str(integration.get("source", "-")), "failed", str(exc))
            )

    return results


def format_verification_results(results: list[dict[str, str]]) -> str:
    """Render verification results as a compact terminal table."""
    lines = ["", "  SERVICE    SOURCE       STATUS      DETAIL"]
    for row in results:
        service = row.get("service", "?")
        source = row.get("source", "-")
        status = row.get("status", "?")
        detail = row.get("detail", "")
        lines.append(f"  {service:<10}{source:<13}{status:<12}{detail}")
    lines.append("")
    return "\n".join(lines)


def verification_exit_code(
    results: list[dict[str, str]],
    *,
    requested_service: str | None = None,
) -> int:
    """Return a CLI exit code for a verification run."""
    if any(row.get("status") == "failed" for row in results):
        return 1
    if requested_service:
        return 1 if any(row.get("status") in {"missing", "failed"} for row in results) else 0
    core_results = [row for row in results if row.get("service") in CORE_VERIFY_SERVICES]
    if not any(row.get("status") == "passed" for row in core_results):
        return 1
    return 0


__all__ = [
    "CORE_VERIFY_SERVICES",
    "SUPPORTED_VERIFY_SERVICES",
    "VerifierFn",
    "format_verification_results",
    "resolve_effective_integrations",
    "verification_exit_code",
    "verify_integrations",
]
