"""Collapsed tool-output peek + expand marker."""

from __future__ import annotations

from infrastructure.terminal.peek import (
    build_output_peek,
    cap_output_for_display,
    format_expand_marker,
    format_view_all_marker,
)


def test_short_output_is_returned_whole_with_nothing_hidden() -> None:
    peek, hidden = build_output_peek("one\ntwo", max_lines=3)
    assert peek == "one\ntwo"
    assert hidden == 0


def test_long_output_is_cut_to_the_head_and_reports_the_remainder() -> None:
    full = "\n".join(f"line {i}" for i in range(10))
    peek, hidden = build_output_peek(full, max_lines=3)
    assert peek == "line 0\nline 1\nline 2"
    assert hidden == 7


def test_empty_output_hides_nothing() -> None:
    assert build_output_peek("   \n  ") == ("", 0)


def test_expand_marker_matches_droid_copy() -> None:
    assert format_expand_marker(7) == "… 7 more, Ctrl+O to view"
    assert format_expand_marker(1) == "… 1 more, Ctrl+O to view"
    assert format_view_all_marker() == "Ctrl+O to view all"


def test_cap_output_uses_one_marker_when_both_caps_apply() -> None:
    line = "y" * 130
    text = "\n".join([line] * 7)
    preview, folded = cap_output_for_display(text)
    assert folded == text
    assert preview.count("Ctrl+O to view") == 1
    assert "output truncated" not in preview
    assert preview.rstrip().endswith("Ctrl+O to view")
    body, _, _ = preview.rpartition("\n")
    assert body.endswith("…")
