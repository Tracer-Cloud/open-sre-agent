"""Agent-first investigation pipeline — stages still run; lifecycle is a facade."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from tools.investigation.agent_pipeline import (
    INVESTIGATION_STAGE_ORDER,
    completed_pipeline_steps,
    run_agent_investigation,
)
from tools.investigation.stages.gather_evidence import ConnectedInvestigationAgent
from tools.investigation.state_factory import make_initial_state


class _QuietAgent(ConnectedInvestigationAgent):
    def run(  # type: ignore[override]
        self,
        state: dict[str, Any],  # noqa: ARG002
        on_event: Any | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {"gather_evidence_ran": True}


@contextmanager
def _stubbed_stages(*, is_noise: bool = False) -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "tools.investigation.stages.resolve_integrations.resolve_integrations",
                return_value={"resolved_integrations": {}},
            )
        )
        stack.enter_context(
            patch(
                "tools.investigation.stages.intake.extract_alert",
                return_value={
                    "is_noise": is_noise,
                    "alert_name": "a",
                    "severity": "warning",
                },
            )
        )
        if not is_noise:
            stack.enter_context(
                patch("tools.investigation.stages.plan_evidence.plan_actions", return_value={})
            )
            stack.enter_context(
                patch(
                    "tools.investigation.stages.diagnose.diagnose",
                    return_value={"root_cause": "rc", "validity_score": 0.5},
                )
            )
            stack.enter_context(
                patch(
                    "tools.investigation.reporting.upstream_correlation.node."
                    "node_correlate_upstream",
                    return_value={},
                )
            )
            stack.enter_context(
                patch(
                    "tools.investigation.reporting.deliver",
                    return_value={
                        "slack_message": "report",
                        "problem_md": "md",
                        "report": "report",
                    },
                )
            )
        yield


def test_run_agent_investigation_records_full_stage_order() -> None:
    state = make_initial_state(raw_alert="alert text")
    with _stubbed_stages():
        out = run_agent_investigation(state, agent_class=_QuietAgent)

    assert list(completed_pipeline_steps(out)) == list(INVESTIGATION_STAGE_ORDER)
    assert out.get("gather_evidence_ran") is True
    assert out.get("root_cause") == "rc"
    assert out.get("slack_message") == "report"


def test_run_agent_investigation_stops_after_intake_on_noise() -> None:
    state = make_initial_state(raw_alert="noise")
    with _stubbed_stages(is_noise=True):
        out = run_agent_investigation(state, agent_class=_QuietAgent)

    assert list(completed_pipeline_steps(out)) == [
        "resolve_integrations",
        "intake",
    ]
    assert out.get("is_noise") is True


def test_run_investigation_uses_agent_pipeline_and_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.investigation.capability import run_investigation

    monkeypatch.setattr(
        "tools.investigation.capability.init_sentry",
        lambda **_kw: None,
    )
    with _stubbed_stages():
        out = run_investigation(raw_alert={"alert": "x"}, agent_class=_QuietAgent)

    assert out["root_cause"] == "rc"
    assert out["slack_message"] == "report"
    assert list(completed_pipeline_steps(out)) == list(INVESTIGATION_STAGE_ORDER)


def test_lifecycle_facade_warns_and_delegates() -> None:
    from tools.investigation.lifecycle import run_connected_investigation

    state = make_initial_state(raw_alert="alert text")
    with (
        pytest.warns(DeprecationWarning, match="deprecated"),
        _stubbed_stages(is_noise=True),
    ):
        out = run_connected_investigation(state, agent_class=_QuietAgent)

    assert out.get("is_noise") is True
    assert list(completed_pipeline_steps(out)) == ["resolve_integrations", "intake"]
