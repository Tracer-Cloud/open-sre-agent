from __future__ import annotations

import json
from pathlib import Path

from tests.benchmarks.realrca_graph.cli import main
from tests.benchmarks.realrca_graph.contract_gaps import (
    build_contract_gap_report,
    render_contract_gap_markdown,
)


def test_contract_gaps_classify_safe_noise_and_feedback_boundaries(tmp_path: Path) -> None:
    paths = _write_contract_gap_fixture(tmp_path)

    report = build_contract_gap_report(
        analogue_path=paths["analogue"],
        baseline_path=paths["baseline"],
        score_boundary_path=paths["score_boundary"],
        dataset_dir=paths["dataset_dir"],
    )

    payload = report.to_dict()
    assert payload["public_validation_truth_used"] is True
    assert payload["hidden_test_reference_used"] is False
    assert report.category_counts["same_mechanism_expression_gap"] == 1
    assert report.category_counts["foreign_public_entity_noise"] == 1
    assert report.category_counts["mechanism_noise"] == 1
    assert report.category_counts["blocked_by_score_feedback"] == 1

    cases = {case.case_id: case for case in report.cases}
    safe = cases["hidden-safe"].items[0]
    assert safe.action == "generate_anchor_only"
    assert "下游接口超时" in safe.safe_hint
    assert safe.foreign_entity_tokens == []

    foreign = cases["hidden-foreign"].items[0]
    assert foreign.action == "use_as_negative_constraint"
    assert "app:validation-pay" in foreign.foreign_entity_tokens

    blocked = cases["hidden-blocked"].items[0]
    assert blocked.category == "blocked_by_score_feedback"

    markdown = render_contract_gap_markdown(report)
    assert "Analogue Contract Gaps" in markdown
    assert "public_validation_truth_used" in markdown


def test_contract_gaps_cli_writes_report(tmp_path: Path) -> None:
    paths = _write_contract_gap_fixture(tmp_path)
    out_json = tmp_path / "contract-gaps.json"
    out_md = tmp_path / "contract-gaps.md"

    assert (
        main(
            [
                "contract-gaps",
                "--analogue",
                str(paths["analogue"]),
                "--baseline",
                str(paths["baseline"]),
                "--score-boundary",
                str(paths["score_boundary"]),
                "--dataset-dir",
                str(paths["dataset_dir"]),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ]
        )
        == 0
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["category_counts"]["same_mechanism_expression_gap"] == 1
    assert out_md.read_text(encoding="utf-8").startswith("# RealRCA Analogue Contract Gaps")


def _write_contract_gap_fixture(tmp_path: Path) -> dict[str, Path]:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "validation_ground_truth.json").write_text(
        json.dumps(
            [
                _truth(
                    "val-timeout",
                    name="下游接口失败",
                    description=(
                        "定位到下游接口 timeout request failed，consumer success_rate 下跌，"
                        "下游服务异常沿调用链传播到入口告警"
                    ),
                ),
                _truth(
                    "val-foreign",
                    name="validation-pay 接口失败",
                    description="validation-pay 的 PaymentFacade timeout request failed",
                ),
                _truth(
                    "val-limit",
                    name="Sentinel限流",
                    description="SentinelBlockException rate limit throttling",
                ),
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "case_id": "hidden-safe",
                        "diagnosis_output": "根因是本案 consumer 侧依赖异常导致入口 HSF 成功率下跌。",
                        "trace_id": "213e050a17861601537764342e7f8f",
                    },
                    {
                        "case_id": "hidden-foreign",
                        "diagnosis_output": "根因是 checkout-app 下游服务异常导致入口告警。",
                        "trace_id": "213e050a17861601537764342e7f8f",
                    },
                    {
                        "case_id": "hidden-noise",
                        "diagnosis_output": "根因是 orders 表慢 SQL。",
                        "trace_id": "213e050a17861601537764342e7f8f",
                    },
                    {
                        "case_id": "hidden-blocked",
                        "diagnosis_output": "根因是本案 consumer 侧依赖异常导致入口 HSF 成功率下跌。",
                        "trace_id": "213e050a17861601537764342e7f8f",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    analogue = tmp_path / "analogues.json"
    analogue.write_text(
        json.dumps(
            {
                "cases": [
                    _case("hidden-safe", ["timeout"], "val-timeout", ["timeout"]),
                    _case("hidden-foreign", ["timeout"], "val-foreign", ["timeout"]),
                    _case("hidden-noise", ["sql"], "val-limit", ["limit"]),
                    _case("hidden-blocked", ["timeout"], "val-timeout", ["timeout"]),
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    score_boundary = tmp_path / "score-boundary.json"
    score_boundary.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "hidden-blocked",
                        "action": "avoid",
                        "blockers": ["negative_tomography_variant"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "dataset_dir": dataset_dir,
        "baseline": baseline,
        "analogue": analogue,
        "score_boundary": score_boundary,
    }


def _truth(case_id: str, *, name: str, description: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "root_cause_chain": [{"description": description}],
        "reference": {
            "required_items": [
                {
                    "name": name,
                    "description": description,
                    "critical": True,
                }
            ]
        },
    }


def _case(
    case_id: str,
    profile_mechanisms: list[str],
    analogue_case_id: str,
    matched_mechanisms: list[str],
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "case_suffix": case_id[-4:],
        "case_type": "HSF",
        "profile": {
            "mechanisms": profile_mechanisms,
            "entities": ["app:checkout-app"],
            "root_labels": ["checkout-app provider timeout"],
            "evidence_preview": ["checkout-app HSF timeout"],
        },
        "matches": [
            {
                "split": "validation",
                "case_id": analogue_case_id,
                "case_type": "HSF",
                "similarity": 0.9,
                "matched_mechanisms": matched_mechanisms,
                "matched_root_kinds": ["pattern_hsf_downstream_timeout"],
                "matched_layers": ["service_dependency"],
            }
        ],
    }
