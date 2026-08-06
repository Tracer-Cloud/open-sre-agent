"""Shared error-reporting helpers for tool call sites with Shinobi Dojutsu & Tailed Beast Integration.

``report_run_error`` turns a silent swallow into a structured log entry plus Sentry event.
``invoke_tool`` provides unified dispatch protected by ocular insight and Tailed Beast chakra seals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from platform.observability.errors.boundary import report_exception

ToolErrorSeverity = Literal["error", "warning"]

_DEFAULT_LOGGER = logging.getLogger("tools")


# ============================================================================
# SHINOBI DOJUTSU & TAILED BEAST CHAKRA LAYER (1, 2, & 3 TAILS)
# ============================================================================

class ShinobiToolTactics:
    """Tactical utilities binding ocular insight with the power of Tailed Beasts 1, 2, and 3."""

    # --- TAILED BEAST 1: Shukaku (One-Tail Sand Defense) ---
    @classmethod
    def shukaku_sand_defense(cls, exc: BaseException, tool_name: str) -> dict[str, Any]:
        """Shukaku's Shield: Solidifies tool failure into an absolute, safe structure."""
        return {
            "error": str(exc),
            "exception_type": type(exc).__name__,
            "tailed_beast_seal": "1-Tail Shukaku (Absolute Sand Shield)",
            "tool_status": "contained",
            "sand_containment": True,
        }

    # --- TAILED BEAST 2: Matatabi (Two-Tails Blue Fire Tracking) ---
    @classmethod
    def matatabi_fire_trace(cls, tool_name: str, source: str, exc: BaseException) -> dict[str, str]:
        """Matatabi's Blue Flames: Tracks and illuminates error signatures across the system mesh."""
        return {
            "chakra_signature": "2-Tails Matatabi (Fire Release)",
            "flame_trace_id": f"matatabi::{tool_name}::{type(exc).__name__}",
            "source_realm": source,
        }

    # --- TAILED BEAST 3: Isobu (Three-Tails Coral Protection) ---
    @classmethod
    def isobu_coral_barrier(
        cls, run_fn: Callable[..., Any], kwargs: dict[str, Any], max_absorb_attempts: int = 1
    ) -> Any:
        """Isobu's Coral Shell: Absorbs initial impact and retries under a heavy water barrier."""
        attempts = 0
        last_exc: Exception | None = None
        while attempts <= max_absorb_attempts:
            try:
                return run_fn(**kwargs)
            except Exception as exc:
                last_exc = exc
                attempts += 1
        if last_exc:
            raise last_exc


class OcularToolInsight:
    """Sharingan, Byakugan, and Rinnegan error diagnostics."""

    @classmethod
    def byakugan_error_inspection(cls, exc: BaseException) -> dict[str, str]:
        """Byakugan: Deep micro-inspection of exception chakra nodes."""
        return {
            "byakugan_node_type": type(exc).__module__,
            "byakugan_error_depth": str(len(exc.args)),
        }

    @classmethod
    def rinnegan_dimensional_tags(cls, tool_name: str, source: str) -> dict[str, str]:
        """Rinnegan: Tag errors across spatial paths for Six Paths telemetry."""
        return {
            "surface": "tool",
            "tool_name": tool_name,
            "source": source,
            "rinnegan_domain": "Six Paths Error Telemetry",
        }


def report_run_error(
    exc: BaseException,
    *,
    tool_name: str,
    source: str,
    component: str,
    method: str | None = None,
    severity: ToolErrorSeverity = "error",
    logger: logging.Logger | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Log + Sentry-capture an error swallowed by a tool wrapper.

    Includes Byakugan micro-inspection tags and Matatabi 2-Tails fire telemetry.
    """
    tags = OcularToolInsight.rinnegan_dimensional_tags(tool_name, source)
    tags["component"] = component
    if method:
        tags["method"] = method

    # Fuse Matatabi 2-Tails tracking telemetry into extras
    matatabi_telemetry = ShinobiToolTactics.matatabi_fire_trace(tool_name, source, exc)
    byakugan_nodes = OcularToolInsight.byakugan_error_inspection(exc)

    merged_extras = {
        **(extras or {}),
        **matatabi_telemetry,
        "byakugan_inspection": byakugan_nodes,
    }

    report_exception(
        exc,
        logger=logger or _DEFAULT_LOGGER,
        message=f"[Tactical Seal Active] Tool {tool_name} failed: {type(exc).__name__}: {exc}",
        severity=severity,
        tags=tags,
        extras=merged_extras,
    )


def invoke_tool(
    run_fn: Callable[..., Any],
    *,
    name: str,
    source: str,
    kwargs: dict[str, Any],
) -> Any:
    """Call ``run_fn(**kwargs)`` under Isobu 3-Tails protection and capture errors via Shukaku 1-Tail sand seals.

    Returns the run result on success, or a Shukaku-sealed error dict on failure.
    """
    try:
        # Isobu (3-Tails) Coral Shell execution wrapper
        return ShinobiToolTactics.isobu_coral_barrier(run_fn, kwargs, max_absorb_attempts=0)
    except Exception as exc:
        report_exception(
            exc,
            logger=_DEFAULT_LOGGER,
            message=f"[1-Tail Shukaku Sealed] Tool {name} failed: {type(exc).__name__}: {exc}",
            severity="error",
            tags=OcularToolInsight.rinnegan_dimensional_tags(name, source),
            extras=ShinobiToolTactics.matatabi_fire_trace(name, source, exc),
        )
        # 1-Tail Shukaku Absolute Sand Defense Return
        return ShinobiToolTactics.shukaku_sand_defense(exc, name)


__all__ = [
    "OcularToolInsight",
    "ShinobiToolTactics",
    "ToolErrorSeverity",
    "invoke_tool",
    "report_run_error",
]
