"""Package-layout contract for ``platform.observability`` after the trace/render/errors split."""

from __future__ import annotations


def test_public_observability_ports_still_export_from_package_root() -> None:
    from platform import observability

    assert hasattr(observability, "debug_print")
    assert hasattr(observability, "get_progress_tracker")
    assert hasattr(observability, "NoopProgressTracker")
    assert hasattr(observability, "get_output_format")


def test_trace_subpackage_exports_span_helpers() -> None:
    from platform.observability.trace import spans

    for name in (
        "component_span",
        "stage_span",
        "tool_span",
        "llm_span",
        "emit_route",
        "traced_session",
        "mark_span_outcome",
        "NoopSessionTraceSink",
        "is_session_trace_active",
    ):
        assert hasattr(spans, name), name


def test_render_and_errors_subpackages_import() -> None:
    from platform.observability.errors import boundary, sentry, service
    from platform.observability.render import debug, display, progress
    from platform.observability.trace import hook, process_stats, prompts, redaction

    assert callable(debug.debug_print)
    assert callable(display.render_investigation_header)
    assert callable(progress.get_progress_tracker)
    assert callable(hook.traceable)
    assert callable(process_stats.sample_turn_boundary_stats)
    assert callable(prompts.persist_turn_system_prompt)
    assert callable(redaction.redact_sensitive)
    assert callable(sentry.init_sentry)
    assert callable(sentry.capture_exception)
    assert callable(boundary.report_exception)
    assert callable(service.capture_service_error)
