"""Integration tests for CLI → observability port wiring."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from infrastructure.harness_ports import (
    _default_fetch_remote,
    reset_harness_ports,
    set_remote_integrations_fetcher,
)
from infrastructure.observability import (
    NoopProgressTracker,
    get_progress_tracker,
    silence_progress_tracker,
)
from infrastructure.observability.render import debug as obs_debug
from infrastructure.observability.render import display as obs_display
from infrastructure.observability.render import progress as obs_progress
from infrastructure.observability.render.debug import set_debug_printer
from infrastructure.observability.render.display import (
    set_investigation_footer_renderer,
    set_investigation_header_renderer,
)
from infrastructure.observability.render.progress import (
    set_progress_tracker,
    set_progress_tracker_factory,
)
from surfaces.shared.terminal.output import boundary as output_boundary
from surfaces.shared.terminal.output import tracker as output_tracker
from surfaces.shared.terminal.output.environment import debug_print
from surfaces.shared.terminal.output.renderers import (
    render_completed_investigation_footer,
    render_investigation_header,
)
from surfaces.shared.terminal.output.tracker import ProgressTracker, get_tracker


def _reset_all_ports() -> None:
    set_progress_tracker(NoopProgressTracker())
    set_progress_tracker_factory(None)
    obs_progress._silenced = False
    set_debug_printer(obs_debug._default_debug_printer)
    set_investigation_header_renderer(obs_display._default_header_renderer)
    set_investigation_footer_renderer(obs_display._default_footer_renderer)
    set_remote_integrations_fetcher(_default_fetch_remote)
    reset_harness_ports()


@pytest.fixture(autouse=True)
def _reset_observability_ports(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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
