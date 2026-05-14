from __future__ import annotations

from app.nodes.root_cause_diagnosis.directives import _build_runbook_section
from app.nodes.root_cause_diagnosis.prompt_builder import build_diagnosis_prompt


def _base_state() -> dict[str, object]:
    return {
        "problem_md": "payments-api OOMKilled.",
        "alert_name": "PaymentsOOM",
        "hypotheses": [],
        "raw_alert": {},
    }


def test_build_runbook_section_returns_empty_when_no_match() -> None:
    assert _build_runbook_section(None) == ""
    assert _build_runbook_section({}) == ""


def test_build_runbook_section_includes_slug_and_body() -> None:
    section = _build_runbook_section({"slug": "payments-oom", "body": "Bump heap to 2G."})

    assert "RELEVANT TEAM RUNBOOK (payments-oom)" in section
    assert "Bump heap to 2G." in section
    assert "Cite the runbook slug" in section


def test_build_runbook_section_truncates_long_body() -> None:
    big = "x" * 5000
    section = _build_runbook_section({"slug": "s", "body": big})

    assert "x" * 2000 in section
    assert "x" * 2001 not in section


def test_diagnosis_prompt_injects_matched_runbook() -> None:
    state = _base_state()
    state["matched_runbook"] = {"slug": "payments-oom", "body": "Page on-call."}

    prompt = build_diagnosis_prompt(state, evidence={})

    assert "RELEVANT TEAM RUNBOOK (payments-oom)" in prompt
    assert "Page on-call." in prompt


def test_diagnosis_prompt_omits_section_without_matched_runbook() -> None:
    prompt = build_diagnosis_prompt(_base_state(), evidence={})

    assert "RELEVANT TEAM RUNBOOK" not in prompt
