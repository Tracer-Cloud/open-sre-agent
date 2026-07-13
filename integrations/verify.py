"""Verification facade: per-service verifiers and the top-level verify_integrations runner.

Verifier callables are sourced from the central plugin registry
(``integrations.verification``). Importing this module triggers
:func:`register_all_verifiers`, which pulls in every integration-local
``@register_verifier`` decorator so the registry is fully populated
before any caller looks anything up.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from rich.markup import escape

from integrations._table_render import new_table, render_table
from integrations._verifiers_loader import register_all_verifiers
from integrations.catalog import (
    resolve_effective_integrations as _resolve_effective_integrations,
)
from integrations.registry import CORE_VERIFY_SERVICES, SUPPORTED_VERIFY_SERVICES
from integrations.slack.verifier import RUNTIME_SEND_TEST_KEY as _SLACK_RUNTIME_SEND_TEST_KEY
from integrations.verification import VerifierFn, get_verifier, result
from platform.terminal.theme import (
    DIM,
    ERROR,
    GLYPH_BULLET,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    GLYPH_WARNING,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
    WARNING,
)

register_all_verifiers()

# Verifiers do blocking network I/O; this caps concurrent connections rather
# than spawning one thread per integration (SUPPORTED_VERIFY_SERVICES is 50+).
_MAX_PARALLEL_VERIFIERS = 16

_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "passed": (GLYPH_SUCCESS, f"bold {HIGHLIGHT}"),
    "failed": (GLYPH_ERROR, f"bold {ERROR}"),
    "missing": (GLYPH_WARNING, f"bold {WARNING}"),
}


def resolve_effective_integrations() -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.opensre and environment variables."""
    return _resolve_effective_integrations()


def _verify_one(
    current_service: str,
    effective_integrations: dict[str, dict[str, Any]],
    *,
    send_slack_test: bool,
) -> dict[str, str]:
    verifier = get_verifier(current_service)
    if verifier is None:
        return result(
            current_service,
            "-",
            "failed",
            "Verification is not supported for this service.",
        )

    integration = effective_integrations.get(current_service)
    if not integration:
        return result(current_service, "-", "missing", "Not configured in local store or env.")

    config = dict(integration["config"])
    if current_service == "slack" and send_slack_test:
        config[_SLACK_RUNTIME_SEND_TEST_KEY] = True

    try:
        return verifier(str(integration["source"]), config)
    except Exception as exc:
        return result(current_service, str(integration.get("source", "-")), "failed", str(exc))


def verify_integrations(
    service: str | None = None,
    *,
    send_slack_test: bool = False,
) -> list[dict[str, str]]:
    """Run verification checks for configured integrations, in parallel.

    Each verifier does blocking network I/O against an independent vendor
    endpoint, so a plain sequential loop pays every verifier's latency in
    series. A thread pool overlaps that I/O; ``executor.map`` still returns
    results in the original ``services`` order.
    """
    effective_integrations = resolve_effective_integrations()
    services = [service] if service else list(SUPPORTED_VERIFY_SERVICES)

    if len(services) == 1:
        return [_verify_one(services[0], effective_integrations, send_slack_test=send_slack_test)]

    with ThreadPoolExecutor(max_workers=min(len(services), _MAX_PARALLEL_VERIFIERS)) as executor:
        return list(
            executor.map(
                lambda current_service: _verify_one(
                    current_service, effective_integrations, send_slack_test=send_slack_test
                ),
                services,
            )
        )


def format_verification_results(results: list[dict[str, str]]) -> str:
    """Render verification results as a theme-consistent Rich table.

    Long ``detail`` text (e.g. the multi-clause OpenClaw bridge hint) is
    folded within its own column instead of overflowing the terminal, which
    is what broke row alignment in the old fixed-width string formatting.
    """
    table = new_table()
    table.add_column("SERVICE", style=TEXT, no_wrap=True)
    table.add_column("SOURCE", style=DIM, no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("DETAIL", style=SECONDARY, overflow="fold", max_width=60)

    for row in results:
        status = row.get("status", "?")
        glyph, style = _STATUS_STYLE.get(status, (GLYPH_BULLET, TEXT))
        table.add_row(
            escape(row.get("service", "?")),
            escape(row.get("source", "-")),
            f"[{style}]{glyph} {escape(status)}[/]",
            escape(row.get("detail", "")),
        )

    return render_table(table)


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
