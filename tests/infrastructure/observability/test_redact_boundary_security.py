"""Security characterization for redact-once sharing.

Pins the invariant that matters for secrets: every external-facing sink
(tracker args, event payload, evidence_entries, tool_outputs) must see a
deep-copied redacted tree — never an alias of the raw tool payload.
``evidence[tool_name]`` still holds the raw result for stage logic.
"""

from __future__ import annotations

from typing import Any

from core.llm.types import ToolCall
from core.state.evidence import EvidenceEntry
from infrastructure.observability.trace.redaction import redact_tool_view
from tools.investigation.stages.gather_evidence.tools import (
    merge_tool_evidence,
    tool_event_payload,
)

_SECRET = "super-secret-credential-value"
_RAW_INPUT = {"namespace": "prod", "token": _SECRET, "safe": "visible"}
_RAW_OUTPUT = {
    "logs": ["ok"],
    "password": _SECRET,
    "nested": {"api_key": _SECRET, "count": 1},
}


def test_redact_tool_view_is_deep_copy_not_alias_of_raw() -> None:
    view = redact_tool_view(_RAW_INPUT, _RAW_OUTPUT)

    assert view.tool_input is not _RAW_INPUT
    assert view.output is not _RAW_OUTPUT
    assert view.output is not None
    assert view.output["nested"] is not _RAW_OUTPUT["nested"]
    # Raw unchanged; redacted has placeholders.
    assert _RAW_INPUT["token"] == _SECRET
    assert view.tool_input["token"] == "[redacted]"
    assert view.output["password"] == "[redacted]"
    assert view.output["nested"]["api_key"] == "[redacted]"
    assert view.output["nested"]["count"] == 1
    assert view.tool_input["safe"] == "visible"


def test_shared_sinks_never_see_raw_secret() -> None:
    tc = ToolCall(id="t1", name="query_logs", input=dict(_RAW_INPUT))
    output: dict[str, Any] = dict(_RAW_OUTPUT)
    output["nested"] = dict(_RAW_OUTPUT["nested"])  # type: ignore[index]

    redacted = redact_tool_view(tc.input, output)
    evidence: dict[str, Any] = {}
    merge_tool_evidence(evidence, tc.name, output, tc.input, redacted=redacted)
    entry = EvidenceEntry(
        key=tc.name,
        data=redacted.output,
        tool_name=tc.name,
        tool_args=redacted.tool_input,
        source="test",
        loop_iteration=0,
    )
    event = tool_event_payload(tc, output=output, redacted=redacted)

    # External-facing / report sinks: no secret.
    for blob in (
        redacted.tool_input,
        redacted.output,
        entry.tool_args,
        entry.data,
        event["input"],
        event["output"],
        evidence["tool_outputs"][0]["tool_args"],
        evidence["tool_outputs"][0]["data"],
    ):
        assert _SECRET not in str(blob)

    # Event + tool_outputs share the one redacted tree (identity).
    assert event["input"] is redacted.tool_input
    assert event["output"] is redacted.output
    assert evidence["tool_outputs"][0]["data"] is redacted.output
    # EvidenceEntry is a Pydantic model — it may copy fields, but values match.
    assert entry.tool_args == redacted.tool_input
    assert entry.data == redacted.output

    # Stage evidence still keeps the raw tool result for investigation logic.
    assert evidence[tc.name] is output
    assert evidence[tc.name]["password"] == _SECRET


def test_tool_event_payload_without_shared_view_still_redacts() -> None:
    tc = ToolCall(id="t1", name="query_logs", input=dict(_RAW_INPUT))
    event = tool_event_payload(tc, output=dict(_RAW_OUTPUT))
    assert event["input"]["token"] == "[redacted]"
    assert event["output"]["password"] == "[redacted]"
    assert _SECRET not in str(event)
