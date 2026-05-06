"""Verbose diagnostics for interactive-shell grounding caches."""

from __future__ import annotations

import logging
import os

_logger = logging.getLogger(__name__)


def log_grounding_cache_diagnostics(reason: str) -> None:
    """Log CLI/docs grounding cache stats when ``TRACER_VERBOSE=1``."""
    if os.environ.get("TRACER_VERBOSE") != "1":
        return
    from app.cli.interactive_shell.cli_reference import get_cli_reference_cache_stats
    from app.cli.interactive_shell.docs_reference import get_docs_cache_stats

    _logger.debug(
        "grounding cache [%s] cli=%s docs=%s",
        reason,
        get_cli_reference_cache_stats(),
        get_docs_cache_stats(),
    )


__all__ = ["log_grounding_cache_diagnostics"]
