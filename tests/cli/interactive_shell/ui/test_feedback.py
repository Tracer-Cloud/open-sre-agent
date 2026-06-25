"""Tests for post-investigation feedback UI."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.interactive_shell.ui.feedback import _format_root_cause_lines, _print_context


def test_format_root_cause_lines_wraps_long_text_without_truncation() -> None:
    root = (
        "The Kubernetes job 'etl-transform-error' for pipeline "
        "'kubernetes_etl_pipeline' failed in namespace 'tracer-test' because "
        "schema validation requires 'payment_method'."
    )

    lines = _format_root_cause_lines(root, cols=60)

    assert len(lines) > 1
    assert all("…" not in line for line in lines)
    assert " ".join(line.strip() for line in lines) == f"Root cause: {root}"


def test_print_context_shows_full_root_cause_in_rich_path() -> None:
    root = (
        "The Kubernetes job 'etl-transform-error' for pipeline "
        "'kubernetes_etl_pipeline' failed because schema validation requires "
        "'payment_method'."
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=60)

    _print_context({"root_cause": root}, console=console)

    output = buf.getvalue()
    assert root in output
    assert "…" not in output
