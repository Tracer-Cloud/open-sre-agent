"""Unit tests for the ``--axis2`` CLI dispatch in ``run_suite``.

Pins the wiring fix for #1672: ``--axis2`` was previously parsed by
``parse_args`` but not consumed by ``run_suite``, so the documented invocation
``python -m tests.synthetic.rds_postgres.run_suite --axis2`` silently produced
the default per-scenario flow with no gap report. These tests would have caught
that regression and now lock the new behavior in place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import tests.synthetic.rds_postgres.run_suite as run_suite_module
from tests.synthetic.mock_grafana_backend.selective_backend import SelectiveGrafanaBackend
from tests.synthetic.rds_postgres.scenario_loader import SUITE_DIR, load_scenario


def _make_score(fixture: Any, *, passed: bool) -> Any:
    base = run_suite_module.score_result(
        fixture,
        {
            "root_cause": "",
            "root_cause_category": "unknown",
            "validated_claims": [],
            "non_validated_claims": [],
            "causal_chain": [],
            "evidence": {},
            "executed_hypotheses": [],
            "investigation_loop_count": 0,
            "report": "",
        },
    )
    # Mutate only the ``passed`` field on a frozen dataclass via ``replace`` to
    # produce both pass and fail variants for combined-exit-code assertions.
    from dataclasses import replace

    return replace(base, passed=passed)


def _empty_state() -> dict[str, Any]:
    return {
        "root_cause": "",
        "root_cause_category": "unknown",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "evidence": {},
        "executed_hypotheses": [],
        "investigation_loop_count": 0,
        "report": "",
    }


def test_axis2_runs_each_fixture_twice_and_prints_gap_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--axis2`` runs every fixture twice and emits ``print_gap_report``."""
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")

    call_args: list[dict[str, Any]] = []

    def _fake_run_scenario(
        f: Any,
        use_mock_grafana: bool = False,
        grafana_backend: Any = None,
    ) -> tuple[dict[str, Any], Any]:
        call_args.append(
            {
                "scenario_id": f.scenario_id,
                "use_mock_grafana": use_mock_grafana,
                "grafana_backend_type": type(grafana_backend).__name__
                if grafana_backend is not None
                else None,
            }
        )
        return _empty_state(), _make_score(f, passed=True)

    print_gap_calls: list[tuple[int, int, int]] = []

    def _fake_print_gap_report(
        axis1_results: list[Any],
        axis2_results: list[Any],
        all_fixtures: list[Any],
    ) -> None:
        print_gap_calls.append((len(axis1_results), len(axis2_results), len(all_fixtures)))

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(run_suite_module, "run_scenario", _fake_run_scenario)
    monkeypatch.setattr(run_suite_module, "print_gap_report", _fake_print_gap_report)

    results = run_suite_module.run_suite(
        [
            "--scenario",
            fixture.scenario_id,
            "--axis2",
            "--observations-dir",
            str(tmp_path),
        ]
    )

    assert len(call_args) == 2, "each fixture must run twice (axis 1 + axis 2)"
    # Axis 1: full mock backend, no explicit grafana_backend.
    assert call_args[0]["use_mock_grafana"] is True
    assert call_args[0]["grafana_backend_type"] is None
    # Axis 2: SelectiveGrafanaBackend injected, mock_grafana False so the
    # selective backend is the only path the agent can hit.
    assert call_args[1]["use_mock_grafana"] is False
    assert call_args[1]["grafana_backend_type"] == SelectiveGrafanaBackend.__name__

    assert print_gap_calls == [(1, 1, 1)]
    # Combined return so main()'s exit-code reflects either axis.
    assert len(results) == 2

    captured = capsys.readouterr()
    assert "Axis 1 vs Axis 2" not in captured.out, (
        "stub print_gap_report swallowed output, so the real banner must not leak"
    )


def test_axis2_combined_results_surface_axis2_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fully-failing axis 2 run must produce a non-zero ``main()`` exit code."""
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")

    invocation = {"count": 0}

    def _fake_run_scenario(
        f: Any,
        use_mock_grafana: bool = False,  # noqa: ARG001
        grafana_backend: Any = None,  # noqa: ARG001
    ) -> tuple[dict[str, Any], Any]:
        invocation["count"] += 1
        # Axis 1 passes, axis 2 fails — combined return must include the failure.
        passed = invocation["count"] == 1
        return _empty_state(), _make_score(f, passed=passed)

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(run_suite_module, "run_scenario", _fake_run_scenario)
    monkeypatch.setattr(run_suite_module, "print_gap_report", lambda *_args, **_kwargs: None)

    exit_code = run_suite_module.main(
        [
            "--scenario",
            fixture.scenario_id,
            "--axis2",
            "--observations-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 1, "axis 2 failure must propagate through main()'s exit code"


def test_axis2_with_json_emits_structured_payload_with_both_axes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--axis2 --json`` emits ``{"axis1": [...], "axis2": [...]}`` on stdout."""
    fixture = load_scenario(SUITE_DIR / "001-replication-lag")

    monkeypatch.setattr(run_suite_module, "load_all_scenarios", lambda _suite_dir: [fixture])
    monkeypatch.setattr(
        run_suite_module,
        "run_scenario",
        lambda f, **_kwargs: (_empty_state(), _make_score(f, passed=True)),
    )
    # The gap-report path must NOT run when --json is set.
    print_gap_calls: list[None] = []
    monkeypatch.setattr(
        run_suite_module,
        "print_gap_report",
        lambda *_args, **_kwargs: print_gap_calls.append(None),
    )

    run_suite_module.run_suite(
        [
            "--scenario",
            fixture.scenario_id,
            "--axis2",
            "--json",
            "--observations-dir",
            str(tmp_path),
        ]
    )

    assert print_gap_calls == [], "--json must short-circuit the human-readable gap report"

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload.keys()) == {"axis1", "axis2"}
    assert len(payload["axis1"]) == 1
    assert len(payload["axis2"]) == 1
    assert payload["axis1"][0]["scenario_id"] == fixture.scenario_id
    assert payload["axis2"][0]["scenario_id"] == fixture.scenario_id
