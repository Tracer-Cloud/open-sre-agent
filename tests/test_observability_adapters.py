"""Integration tests for CLI → observability port wiring."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from cli.interactive_shell.ui.output import boundary as output_boundary
from cli.interactive_shell.ui.output import tracker as output_tracker
from cli.interactive_shell.ui.output.environment import debug_print
from cli.interactive_shell.ui.output.renderers import (
    render_completed_investigation_footer,
    render_investigation_header,
)
from cli.interactive_shell.ui.output.tracker import ProgressTracker, get_tracker
from integrations import port as integrations_port
from integrations.port import set_remote_integrations_fetcher
from platform.observability import (
    NoopProgressTracker,
    get_progress_tracker,
    silence_progress_tracker,
)
from platform.observability import debug as obs_debug
from platform.observability import display as obs_display
from platform.observability import progress as obs_progress
from platform.observability.debug import set_debug_printer
from platform.observability.display import (
    set_investigation_footer_renderer,
    set_investigation_header_renderer,
)
from platform.observability.progress import (
    set_progress_tracker,
    set_progress_tracker_factory,
)


def _reset_all_ports() -> None:
    """Restore every port + global to its no-op / default state."""
    set_progress_tracker(NoopProgressTracker())
    set_progress_tracker_factory(None)
    obs_progress._silenced = False
    set_debug_printer(obs_debug._default_debug_printer)
    set_investigation_header_renderer(obs_display._default_header_renderer)
    set_investigation_footer_renderer(obs_display._default_footer_renderer)
    # ``install_product_adapters`` now also wires the integrations
    # fetcher; reset it here so this file's tests don't leak the
    # Tracer adapter into other tests in the session.
    set_remote_integrations_fetcher(integrations_port._default_fetcher)


@pytest.fixture(autouse=True)
def _reset_observability_ports(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give each test a clean port state — setup AND teardown.

    The teardown matters: without it, the final test in this file
    leaves whatever adapters ``install_product_adapters()`` registered
    in place for the rest of the pytest session, polluting any later
    test that touches the observability or integrations ports.
    """
    for name in ("TRACER_OUTPUT_FORMAT", "NO_COLOR", "SLACK_WEBHOOK_URL", "TRACER_VERBOSE"):
        monkeypatch.delenv(name, raising=False)
    _reset_all_ports()
    monkeypatch.setattr(output_tracker, "_tracker", None)
    yield
    _reset_all_ports()


def test_ports_default_to_noop_before_cli_install() -> None:
    assert isinstance(get_progress_tracker(), NoopProgressTracker)


def test_install_product_adapters_wires_progress_tracker() -> None:
    output_boundary.install_product_adapters()

    tracker = get_progress_tracker()
    assert isinstance(tracker, ProgressTracker)
    assert tracker is get_tracker()


def test_install_product_adapters_wires_debug_and_display() -> None:
    output_boundary.install_product_adapters()

    assert obs_debug._printer is debug_print
    assert obs_display._header_renderer is render_investigation_header
    assert obs_display._footer_renderer is render_completed_investigation_footer


def test_sync_pipeline_path_records_progress_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core ``get_progress_tracker()`` must drive the CLI tracker in sync runs."""
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")
    output_boundary.install_product_adapters()

    tracker = get_progress_tracker()
    tracker.start("extract_alert", "Parsing alert payload")
    tracker.complete("extract_alert", message="done")

    assert len(tracker.events) == 2
    assert tracker.events[0].node_name == "extract_alert"
    assert tracker.events[0].status == "started"
    assert tracker.events[1].status == "completed"


def test_silence_progress_tracker_blocks_lazy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")
    output_boundary.install_product_adapters()

    silence_progress_tracker()
    tracker = get_progress_tracker()
    tracker.start("extract_alert")

    assert isinstance(tracker, NoopProgressTracker)
