from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.nodes.investigate.models import InvestigateInput
from app.nodes.plan_actions import node as node_module


class _TrackerStub:
    def start(self, _name: str, _message: str) -> None:
        return

    def complete(self, _name: str, *, fields_updated: list[str], message: str) -> None:
        assert message
        self.fields_updated = fields_updated


def _patch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "opensre_home"
    monkeypatch.setattr("app.constants.OPENSRE_HOME_DIR", home)
    return home


def _write_runbook(home: Path, slug: str, frontmatter: str, body: str = "body") -> None:
    target = home / "runbooks" / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def _stub_plan_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = node_module.InvestigationPlan(
        actions=["get_logs"],
        rationale="Logs around failures.",
        retrieval_controls=None,
    )
    monkeypatch.setattr(
        node_module.InvestigateInput,
        "from_state",
        lambda _state: InvestigateInput(raw_alert={}, context={}, tool_budget=10),
    )
    monkeypatch.setattr(
        node_module,
        "build_plan_actions",
        lambda **_kwargs: (plan, {"knowledge": {}}, ["get_logs"], [], False, "", []),
    )
    monkeypatch.setattr(node_module, "get_tracker", lambda: _TrackerStub())


def test_node_plan_actions_attaches_matched_runbook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _patch_home(monkeypatch, tmp_path)
    _write_runbook(
        home,
        "payments-oom",
        frontmatter="service: payments-api\ntriggers:\n  - oom\n  - memory",
    )
    _stub_plan_actions(monkeypatch)

    state: dict[str, Any] = {
        "raw_alert": {},
        "context": {},
        "resolved_integrations": {},
        "investigation_loop_count": 0,
        "problem_md": "payments-api OOMKilled with memory exhaustion.",
        "alert_name": "PaymentsOOM",
        "pipeline_name": "payments-api",
        "alert_json": {"commonLabels": {"service": "payments-api"}},
    }

    result = node_module.node_plan_actions(state)

    matched = result["matched_runbook"]
    assert matched is not None
    assert matched["slug"] == "payments-oom"
    assert matched["service"] == "payments-api"


def test_node_plan_actions_matched_runbook_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_home(monkeypatch, tmp_path)
    _stub_plan_actions(monkeypatch)

    result = node_module.node_plan_actions(
        {
            "raw_alert": {},
            "context": {},
            "resolved_integrations": {},
            "investigation_loop_count": 0,
            "problem_md": "",
            "alert_name": "SomethingElse",
            "pipeline_name": "other",
        }
    )

    assert result["matched_runbook"] is None
