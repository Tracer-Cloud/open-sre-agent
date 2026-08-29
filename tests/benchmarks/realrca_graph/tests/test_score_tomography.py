from __future__ import annotations

import json

from tests.benchmarks.realrca_graph.score_tomography import (
    build_tomography_report,
    render_tomography_markdown,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _row(case_id: str, diagnosis: str, trace_id: str = "trace") -> dict[str, str]:
    return {"case_id": case_id, "diagnosis_output": diagnosis, "trace_id": trace_id}


def _submission(agent_name: str) -> dict[str, object]:
    return {
        "submission_response": {
            "submission": {
                "agent_name": agent_name,
                "team_name": "隐元玩一玩",
            }
        }
    }


def test_tomography_infers_direct_and_single_unknown_deltas(tmp_path) -> None:
    case_a = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    case_b = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    case_c = "01a0330f-29a8-7e83-8121-3bf4cce321cc"
    reference_rows = [
        _row(case_a, "reference a"),
        _row(case_b, "reference b"),
        _row(case_c, "reference c"),
    ]
    _write_json(tmp_path / "reference.json", {"results": reference_rows})
    _write_json(
        tmp_path / "leaderboard.json",
        {
            "items": [
                {"team_name": "隐元玩一玩", "agent_name": "ref", "accuracy": 10.0},
                {"team_name": "隐元玩一玩", "agent_name": "probe-anchor", "accuracy": 9.0},
                {"team_name": "隐元玩一玩", "agent_name": "probe-target", "accuracy": 8.5},
                {"team_name": "隐元玩一玩", "agent_name": "probe-combo", "accuracy": 10.0},
            ]
        },
    )
    _write_json(tmp_path / "submission-test-anchor.json", _submission("probe-anchor"))
    _write_json(
        tmp_path / "results-test-anchor.json",
        {
            "results": [
                _row(case_a, "worse a"),
                _row(case_b, "reference b"),
                _row(case_c, "reference c"),
            ]
        },
    )
    _write_json(tmp_path / "submission-test-target.json", _submission("probe-target"))
    _write_json(
        tmp_path / "results-test-target.json",
        {
            "results": [
                _row(case_a, "reference a"),
                _row(case_b, "worse b"),
                _row(case_c, "reference c"),
            ]
        },
    )
    _write_json(tmp_path / "submission-test-combo.json", _submission("probe-combo"))
    _write_json(
        tmp_path / "results-test-combo.json",
        {
            "results": [
                _row(case_a, "worse a"),
                _row(case_b, "reference b"),
                _row(case_c, "better c"),
            ]
        },
    )

    report = build_tomography_report(
        leaderboard_path=tmp_path / "leaderboard.json",
        reference_result_path=tmp_path / "reference.json",
        results_dir=tmp_path,
        reference_agent_name="ref",
    )

    by_suffix = {case.case_suffix: case for case in report.cases}
    assert by_suffix["21aa"].best_estimate == -1.0
    assert by_suffix["21bb"].best_estimate == -1.5
    assert by_suffix["21cc"].best_estimate == 1.0
    assert by_suffix["21cc"].estimates[0].methods == ["constraint_single_unknown"]
    assert report.positive_answer_count == 1


def test_render_tomography_markdown_shows_positive_cases(tmp_path) -> None:
    case_id = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    _write_json(tmp_path / "reference.json", {"results": [_row(case_id, "reference")]})
    _write_json(
        tmp_path / "leaderboard.json",
        {
            "items": [
                {"team_name": "隐元玩一玩", "agent_name": "ref", "accuracy": 10.0},
                {"team_name": "隐元玩一玩", "agent_name": "probe-a", "accuracy": 11.0},
            ]
        },
    )
    _write_json(tmp_path / "submission-test-a.json", _submission("probe-a"))
    _write_json(tmp_path / "results-test-a.json", {"results": [_row(case_id, "better")]})

    report = build_tomography_report(
        leaderboard_path=tmp_path / "leaderboard.json",
        reference_result_path=tmp_path / "reference.json",
        results_dir=tmp_path,
        reference_agent_name="ref",
    )

    markdown = render_tomography_markdown(report)

    assert "positive_answers: `1`" in markdown
    assert "`21aa`" in markdown


def test_tomography_reads_multiple_result_directories(tmp_path) -> None:
    case_a = "01a0330f-29a8-7e83-8121-3bf4cce321aa"
    case_b = "01a0330f-29a8-7e83-8121-3bf4cce321bb"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    _write_json(
        tmp_path / "reference.json",
        {"results": [_row(case_a, "reference a"), _row(case_b, "reference b")]},
    )
    _write_json(
        tmp_path / "leaderboard.json",
        {
            "items": [
                {"team_name": "隐元玩一玩", "agent_name": "ref", "accuracy": 10.0},
                {"team_name": "隐元玩一玩", "agent_name": "probe-a", "accuracy": 9.0},
                {"team_name": "隐元玩一玩", "agent_name": "probe-b", "accuracy": 11.0},
            ]
        },
    )
    _write_json(first_dir / "submission-test-a.json", _submission("probe-a"))
    _write_json(
        first_dir / "results-test-a.json",
        {"results": [_row(case_a, "worse a"), _row(case_b, "reference b")]},
    )
    _write_json(second_dir / "submission-test-b.json", _submission("probe-b"))
    _write_json(
        second_dir / "results-test-b.json",
        {"results": [_row(case_a, "reference a"), _row(case_b, "better b")]},
    )

    report = build_tomography_report(
        leaderboard_path=tmp_path / "leaderboard.json",
        reference_result_path=tmp_path / "reference.json",
        results_dir=[first_dir, second_dir],
        reference_agent_name="ref",
    )

    by_suffix = {case.case_suffix: case for case in report.cases}
    assert report.matched_submission_count == 2
    assert by_suffix["21aa"].best_estimate == -1.0
    assert by_suffix["21bb"].best_estimate == 1.0
