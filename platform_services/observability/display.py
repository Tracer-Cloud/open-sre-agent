"""Investigation-display port — header render + injection.

Core stages call :func:`render_investigation_header` at the start of
an investigation to announce its alert. The default is a no-op; the
CLI registers a Rich-panel adapter at boundary so the same call
produces the styled banner the REPL expects.
"""

from __future__ import annotations

from collections.abc import Callable

InvestigationHeaderRenderer = Callable[[str, str, str, str | None], None]
InvestigationFooterRenderer = Callable[[], None]


def _default_header_renderer(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    alert_id: str | None = None,
) -> None:
    _ = (alert_name, pipeline_name, severity, alert_id)


def _default_footer_renderer() -> None:
    return None


_header_renderer: InvestigationHeaderRenderer = _default_header_renderer
_footer_renderer: InvestigationFooterRenderer = _default_footer_renderer


def render_investigation_header(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    alert_id: str | None = None,
) -> None:
    """Render the investigation start banner via the registered adapter."""
    _header_renderer(alert_name, pipeline_name, severity, alert_id)


def render_completed_investigation_footer() -> None:
    """Render the post-report footer via the registered adapter."""
    _footer_renderer()


def set_investigation_header_renderer(renderer: InvestigationHeaderRenderer) -> None:
    """Install ``renderer`` as the active investigation-header implementation."""
    global _header_renderer
    _header_renderer = renderer


def set_investigation_footer_renderer(renderer: InvestigationFooterRenderer) -> None:
    """Install ``renderer`` as the active investigation-footer implementation."""
    global _footer_renderer
    _footer_renderer = renderer
