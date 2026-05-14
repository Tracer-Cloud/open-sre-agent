"""Synthetic runbook suite — fixture-driven, no live infra.

Each scenario directory under ``tests/synthetic/runbooks/`` contains:
- scenario.yml   metadata (service, pipeline_name, failure_mode)
- alert.json     the alert payload, mirrors shape used by extract_alert
- runbook.md     the runbook expected to match
- answer.yml     assertions about retrieval + provenance rendering

The test:
1. Copies ``runbook.md`` into a tmp OPENSRE_HOME / runbooks dir.
2. Runs ``retrieve_matching_runbook`` with keywords/service derived from the alert.
3. Asserts the matched slug + provenance line match the answer key.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from app.nodes.plan_actions.extract_keywords import extract_keywords
from app.nodes.publish_findings.formatters.report import format_slack_message
from app.nodes.publish_findings.report_context import build_report_context
from app.runbooks.retrieval import retrieve_matching_runbook
from app.runbooks.store import load_all

SUITE_DIR = Path(__file__).resolve().parent


def _scenarios() -> list[Path]:
    return sorted(p for p in SUITE_DIR.iterdir() if p.is_dir() and p.name[0].isdigit())


@pytest.mark.parametrize("scenario_dir", _scenarios(), ids=lambda p: p.name)
def test_runbook_suite_scenario(
    scenario_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenario = yaml.safe_load((scenario_dir / "scenario.yml").read_text(encoding="utf-8"))
    alert = json.loads((scenario_dir / "alert.json").read_text(encoding="utf-8"))
    answer = yaml.safe_load((scenario_dir / "answer.yml").read_text(encoding="utf-8"))

    home = tmp_path / "opensre_home"
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", home)
    target_runbooks = home / "runbooks"
    target_runbooks.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        scenario_dir / "runbook.md",
        target_runbooks / f"{answer['expected_matched_slug']}.md",
    )

    common_labels = alert.get("commonLabels", {})
    common_annotations = alert.get("commonAnnotations", {})
    problem_md = (
        common_annotations.get("summary", "") + " " + common_annotations.get("description", "")
    )
    alert_name = common_labels.get("alertname", "")

    keywords = extract_keywords(problem_md, alert_name)
    matched = retrieve_matching_runbook(
        runbooks=load_all(),
        keywords=keywords,
        service=common_labels.get("service"),
        pipeline_name=scenario.get("pipeline_name"),
    )

    assert matched is not None
    assert matched.slug == answer["expected_matched_slug"]
    assert matched.service == answer["expected_service"]
    assert set(matched.triggers) >= set(answer["expected_triggers"])

    state = {
        "pipeline_name": scenario.get("pipeline_name", ""),
        "alert_name": alert_name,
        "root_cause": "JVM heap exhaustion in payments-api.",
        "root_cause_category": "resource_exhaustion",
        "validated_claims": [],
        "non_validated_claims": [],
        "investigation_recommendations": [],
        "remediation_steps": ["Bump JVM -Xmx from 1.5G to 2G (per runbook payments-oom)."],
        "available_sources": {},
        "evidence": {},
        "raw_alert": alert,
        "matched_runbook": matched.to_dict(),
    }

    ctx = build_report_context(state)
    assert ctx["runbook_provenance"] is not None
    assert ctx["runbook_provenance"]["slug"] == answer["expected_matched_slug"]

    message = format_slack_message(ctx)
    assert answer["expected_provenance_line"] in message
