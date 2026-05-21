"""Unit tests for render_report_dir — markdown + HTML rendering of report.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.benchmarks._framework.reporting import render_report_dir

# --------------------------------------------------------------------------- #
# Helpers — build minimal but realistic run directory                          #
# --------------------------------------------------------------------------- #


def _write_report_json(run_dir: Path) -> dict:
    """Write a representative report.json + cases/*.json into ``run_dir``.

    Shape matches what runner._report_to_dict emits so the test doubles as a
    smoke against the contract between runner and reporter.
    """
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": "dev-2026-01-01T00-00-00Z_cloudopsbench",
        "config_hash": "abc123",
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:05:00+00:00",
        "per_stratum": {
            "all": {
                "opensre+llm/claude_sonnet": {"a1": 0.65, "grounding": 0.80},
                "opensre+llm/gpt_4o": {"a1": 0.52, "grounding": 0.71},
            },
            "seen-shape": {
                "opensre+llm/claude_sonnet": {"a1": 0.78, "grounding": 0.85},
            },
            "unseen-shape": {
                "opensre+llm/claude_sonnet": {"a1": 0.45, "grounding": 0.70},
            },
        },
        "reported_metrics": ["a1", "grounding"],
        "pre_registration_path": str(run_dir / "prereg.md"),
        "raw_artifacts_dir": str(cases_dir),
        "negative_results": "On unseen-shape, opensre tied LLM-alone on 3/10 cases.",
        "coi_disclosure": "Built and run by the opensre team. <test fixture>.",
        "cost": {
            "budget_usd": 100.0,
            "total_cost_usd": 12.34,
            "remaining_usd": 87.66,
            "total_tokens_in": 1_000_000,
            "total_tokens_out": 500_000,
            "total_calls": 42,
            "by_model": {
                "claude-sonnet-4-5-20250929": {
                    "tokens_in": 600_000,
                    "tokens_out": 300_000,
                    "cost_usd": 6.30,
                    "call_count": 25,
                },
                "gpt-4o-2024-11-20": {
                    "tokens_in": 400_000,
                    "tokens_out": 200_000,
                    "cost_usd": 6.04,
                    "call_count": 17,
                },
            },
        },
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=2))

    # One per-case artifact, exercising the case-loading branch
    cell = {
        "case_id": "case-001",
        "mode": "opensre+llm",
        "llm": "claude_sonnet",
        "run_index": 0,
        "metrics": {"a1": 1.0, "grounding": 0.9},
        "ok": True,
    }
    (cases_dir / "case-001__opensre+llm__claude_sonnet__0.json").write_text(json.dumps(cell))
    return report


# --------------------------------------------------------------------------- #
# Default rendering                                                            #
# --------------------------------------------------------------------------- #


def test_render_report_dir_produces_markdown_and_html_by_default(tmp_path: Path) -> None:
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path)
    assert set(out.keys()) == {"markdown", "html"}
    assert out["markdown"].exists()
    assert out["html"].exists()
    assert out["markdown"].stat().st_size > 0
    assert out["html"].stat().st_size > 0


def test_render_report_dir_writes_to_canonical_filenames(tmp_path: Path) -> None:
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path)
    assert out["markdown"] == tmp_path / "report.md"
    assert out["html"] == tmp_path / "report.html"


def test_render_report_dir_missing_report_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="report.json"):
        render_report_dir(tmp_path)


# --------------------------------------------------------------------------- #
# Format filtering                                                             #
# --------------------------------------------------------------------------- #


def test_render_report_dir_markdown_only(tmp_path: Path) -> None:
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["markdown"])
    assert set(out.keys()) == {"markdown"}
    assert not (tmp_path / "report.html").exists()


def test_render_report_dir_html_only(tmp_path: Path) -> None:
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["html"])
    assert set(out.keys()) == {"html"}
    assert not (tmp_path / "report.md").exists()


# --------------------------------------------------------------------------- #
# Content checks — integrity-respecting rendering                              #
# --------------------------------------------------------------------------- #


def test_markdown_contains_per_stratum_breakdown(tmp_path: Path) -> None:
    """Mechanism 4: reporter must surface per-stratum, not just aggregate."""
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["markdown"])
    md = out["markdown"].read_text()
    assert "seen-shape" in md
    assert "unseen-shape" in md


def test_markdown_contains_negative_results_verbatim(tmp_path: Path) -> None:
    """Mechanism 9: negative results render verbatim."""
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["markdown"])
    md = out["markdown"].read_text()
    assert "On unseen-shape, opensre tied LLM-alone on 3/10 cases." in md


def test_markdown_contains_cost_breakdown(tmp_path: Path) -> None:
    """Mechanism 11 / Pillar 0: cost shown per-model, not aggregated away."""
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["markdown"])
    md = out["markdown"].read_text()
    assert "claude-sonnet-4-5-20250929" in md
    assert "gpt-4o-2024-11-20" in md
    assert "12.34" in md  # total cost


def test_html_escapes_user_supplied_strings(tmp_path: Path) -> None:
    """COI disclosure includes '<test fixture>' — must be HTML-escaped."""
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["html"])
    html = out["html"].read_text()
    # The raw substring "<test fixture>" should NOT appear unescaped in the HTML
    assert "<test fixture>" not in html
    # But the escaped form should
    assert "&lt;test fixture&gt;" in html


def test_html_includes_inline_style_no_external_deps(tmp_path: Path) -> None:
    """Self-contained HTML — no external CSS / JS / image references."""
    _write_report_json(tmp_path)
    out = render_report_dir(tmp_path, formats=["html"])
    html = out["html"].read_text()
    # Has inline <style>
    assert "<style" in html
    # No external script/link tags that would break offline viewing
    assert '<link rel="stylesheet"' not in html
    assert "<script src=" not in html


def test_render_works_without_cases_directory(tmp_path: Path) -> None:
    """report.json is the source of truth; cases/ is optional."""
    _write_report_json(tmp_path)
    # Remove the cases dir
    import shutil

    shutil.rmtree(tmp_path / "cases")
    out = render_report_dir(tmp_path)
    assert out["markdown"].exists()
    assert out["html"].exists()
