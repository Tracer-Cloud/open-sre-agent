"""Keep uvicorn's HTTP access log signal-bearing under Kubernetes probes.

A liveness/readiness probe hits ``/health`` and ``/healthz`` every few seconds,
so on a long-running pod the access log becomes a wall of identical 200s that
say nothing. A *failing* probe, on the other hand, is exactly what an operator
needs to see — so only successful probe requests are dropped here; anything
4xx/5xx, and every non-probe route, logs unchanged.

Sibling of :mod:`gateway.core.config.logging_config`, which does the same job for the
gateway daemon's own package logs.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

# Endpoints served purely for orchestrator probes (see gateway/web/webapp.py).
# ``/`` is deliberately excluded: it doubles as the human-facing root.
_PROBE_PATHS = frozenset({"/health", "/healthz", "/readyz", "/ok"})

_ACCESS_LOGGER_NAME = "uvicorn.access"

# uvicorn logs access lines with positional args
# ``(client_addr, method, full_path, http_version, status_code)``.
_PATH_ARG_INDEX = 2
_STATUS_ARG_INDEX = 4


class ProbeAccessLogFilter(logging.Filter):
    """Drop successful liveness/readiness probe hits from the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) <= _STATUS_ARG_INDEX:
            return True

        path = str(args[_PATH_ARG_INDEX]).split("?", 1)[0]
        if path not in _PROBE_PATHS:
            return True

        status = args[_STATUS_ARG_INDEX]
        if not isinstance(status, int):
            return True

        # Keep failures; a probe that started returning 503 is the whole point.
        return status >= HTTPStatus.BAD_REQUEST


def install_probe_access_log_filter() -> None:
    """Attach :class:`ProbeAccessLogFilter` to uvicorn's access logger.

    Idempotent, so importing the app more than once in a process (tests, the
    interactive shell's embedded server) does not stack duplicate filters.
    """
    access_logger = logging.getLogger(_ACCESS_LOGGER_NAME)
    if any(isinstance(existing, ProbeAccessLogFilter) for existing in access_logger.filters):
        return
    access_logger.addFilter(ProbeAccessLogFilter())


__all__ = ["ProbeAccessLogFilter", "install_probe_access_log_filter"]
