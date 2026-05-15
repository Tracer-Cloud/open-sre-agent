"""Unit tests for the channel-agnostic section pipeline."""

from __future__ import annotations

from typing import Any

from app.delivery.publish_findings.formatters.sections import (
    Section,
    SectionKind,
    build_sections,
    dedupe_sections,
    prepare_sections_for,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _header(
    *,
    alert: str = "PodCrash",
    pipeline: str = "ingest",
    severity: str = "critical",
) -> Section:
    return Section(
        kind=SectionKind.SEVERITY_HEADER,
        extras={"alert_name": alert, "pipeline_name": pipeline, "severity": severity},
    )


def _root_cause(*, body: str, top_log: str | None = None) -> Section:
    extras: dict[str, Any] = {"top_log": top_log} if top_log else {}
    return Section(kind=SectionKind.ROOT_CAUSE, body=body, extras=extras)


# ---------------------------------------------------------------------------
# dedupe_sections — banner-noise detection
# ---------------------------------------------------------------------------


def test_dedupe_drops_root_cause_that_only_restates_alert_and_pipeline() -> None:
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="PodCrash on ingest (severity: critical)"),
    ]
    result = dedupe_sections(sections)
    kinds = [s.kind for s in result]
    assert kinds == [SectionKind.SEVERITY_HEADER]


def test_dedupe_keeps_root_cause_when_sentence_has_causal_language() -> None:
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="PodCrash on ingest because the container ran out of memory"),
    ]
    result = dedupe_sections(sections)
    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER, SectionKind.ROOT_CAUSE]


def test_dedupe_keeps_root_cause_when_sentence_is_long_and_content_rich() -> None:
    long_body = (
        "PodCrash on ingest produced 47 consecutive failures over the last hour, "
        "with the underlying container exiting with code 137 and the kubelet "
        "logging OOMKilled events for the same pod repeatedly."
    )
    sections = [_header(alert="PodCrash", pipeline="ingest"), _root_cause(body=long_body)]
    result = dedupe_sections(sections)
    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER, SectionKind.ROOT_CAUSE]


def test_dedupe_clears_body_but_keeps_section_when_top_log_present() -> None:
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(
            body="PodCrash on ingest (severity: critical)",
            top_log="container exited with code 137: OOMKilled",
        ),
    ]
    result = dedupe_sections(sections)

    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER, SectionKind.ROOT_CAUSE]
    root = result[1]
    assert root.body is None
    assert root.extras["top_log"] == "container exited with code 137: OOMKilled"


def test_dedupe_noop_when_no_severity_header() -> None:
    sections = [_root_cause(body="Something happened on the cluster")]
    result = dedupe_sections(sections)
    assert result == sections


def test_dedupe_preserves_not_determined_fallback() -> None:
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="Not determined (insufficient evidence)."),
    ]
    result = dedupe_sections(sections)
    assert len(result) == 2
    assert (result[1].body or "").startswith("Not determined")


def test_dedupe_skips_when_header_missing_alert_or_pipeline() -> None:
    """Without both fields the heuristic cannot decide what is redundant."""
    sections = [
        _header(alert="", pipeline="ingest"),
        _root_cause(body="on ingest"),
    ]
    result = dedupe_sections(sections)
    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER, SectionKind.ROOT_CAUSE]


def test_dedupe_keeps_root_cause_when_pipeline_name_missing_from_sentence() -> None:
    """If the sentence does not mention the pipeline, it is carrying new information."""
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="PodCrash hit the OOM threshold"),
    ]
    result = dedupe_sections(sections)
    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER, SectionKind.ROOT_CAUSE]


def test_dedupe_short_sentence_without_severity_marker_still_dropped() -> None:
    """< 120 chars + contains both alert+pipeline + no causal words → banner restate."""
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="PodCrash on ingest"),
    ]
    result = dedupe_sections(sections)
    assert [s.kind for s in result] == [SectionKind.SEVERITY_HEADER]


def test_dedupe_returns_new_list_not_mutating_input() -> None:
    sections = [
        _header(alert="PodCrash", pipeline="ingest"),
        _root_cause(body="PodCrash on ingest (severity: critical)"),
    ]
    original = list(sections)
    dedupe_sections(sections)
    assert sections == original


# ---------------------------------------------------------------------------
# build_sections — ordering and conditional emission
# ---------------------------------------------------------------------------


def test_build_sections_emits_in_canonical_order() -> None:
    ctx: dict[str, Any] = {
        "severity": "high",
        "alert_name": "DiskFull",
        "pipeline_name": "etl",
        "root_cause": "Disk full because writes outpaced retention",
        "validated_claims": [{"claim": "Evidence shows full disk", "evidence_ids": []}],
        "non_validated_claims": [{"claim": "Possibly related to traffic spike"}],
        "remediation_steps": ["Expand volume", "Compress logs"],
        "evidence": {},
    }
    kinds = [s.kind for s in build_sections(ctx)]

    assert kinds.count(SectionKind.CLAIMS) == 2
    assert kinds.index(SectionKind.SEVERITY_HEADER) < kinds.index(SectionKind.ROOT_CAUSE)
    assert kinds.index(SectionKind.ROOT_CAUSE) < kinds.index(SectionKind.CLAIMS)
    assert kinds.index(SectionKind.CLAIMS) < kinds.index(SectionKind.REMEDIATION)


def test_build_sections_omits_empty_optional_sections() -> None:
    ctx: dict[str, Any] = {
        "severity": "info",
        "alert_name": "Heartbeat",
        "pipeline_name": "watchdog",
        "root_cause": "Heartbeat tick observed on watchdog",
        "validated_claims": [],
        "non_validated_claims": [],
        "remediation_steps": [],
        "evidence": {},
    }
    kinds = {s.kind for s in build_sections(ctx)}
    assert SectionKind.SEVERITY_HEADER in kinds
    assert SectionKind.CLAIMS not in kinds
    assert SectionKind.REMEDIATION not in kinds
    assert SectionKind.PROVENANCE not in kinds


def test_build_sections_severity_header_carries_alert_pipeline_and_severity() -> None:
    ctx: dict[str, Any] = {
        "severity": "warning",
        "alert_name": "CPUHigh",
        "pipeline_name": "api",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    sections = build_sections(ctx)
    header = next(s for s in sections if s.kind is SectionKind.SEVERITY_HEADER)
    assert header.extras["alert_name"] == "CPUHigh"
    assert header.extras["pipeline_name"] == "api"
    assert header.extras["severity"] == "warning"


def test_build_sections_skips_severity_header_when_alert_or_pipeline_missing() -> None:
    ctx: dict[str, Any] = {
        "severity": "critical",
        "alert_name": "",
        "pipeline_name": "api",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    kinds = {s.kind for s in build_sections(ctx)}
    assert SectionKind.SEVERITY_HEADER not in kinds


def test_build_sections_meta_section_omitted_when_empty() -> None:
    ctx: dict[str, Any] = {
        "severity": "low",
        "alert_name": "Quiet",
        "pipeline_name": "noop",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    kinds = {s.kind for s in build_sections(ctx)}
    assert SectionKind.META not in kinds


def test_build_sections_emits_fallback_root_cause_when_no_sentence_or_log() -> None:
    """Regression: when an investigation has neither a derived root cause
    sentence nor a top error log, the ROOT_CAUSE section must still be emitted
    with the "Not determined (insufficient evidence)." fallback. The legacy
    per-formatter code always rendered this fallback; the section pipeline
    must preserve that signal so the user knows the investigation completed
    without identifying a cause (vs the section being silently dropped)."""
    ctx: dict[str, Any] = {
        "severity": "info",
        "alert_name": "Heartbeat",
        "pipeline_name": "watchdog",
        # No root_cause text and no validated_claims → no derived sentence.
        "root_cause": "",
        "validated_claims": [],
        "non_validated_claims": [],
        # No evidence → no top_log.
        "evidence": {},
    }
    sections = build_sections(ctx)
    root = next((s for s in sections if s.kind is SectionKind.ROOT_CAUSE), None)
    assert root is not None, "ROOT_CAUSE section must be emitted even with empty inputs"
    assert root.body == "Not determined (insufficient evidence)."
    assert root.extras == {}


def test_prepare_sections_for_keeps_fallback_root_cause_even_with_severity_header() -> None:
    """The dedup heuristic must not drop the "Not determined" fallback even
    when a SEVERITY_HEADER is present — banner restate detection only fires
    for sentences that quote the alert+pipeline pair."""
    ctx: dict[str, Any] = {
        "severity": "critical",
        "alert_name": "PodCrash",
        "pipeline_name": "ingest",
        "root_cause": "",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    sections = prepare_sections_for(ctx)
    kinds = [s.kind for s in sections]
    assert SectionKind.SEVERITY_HEADER in kinds
    assert SectionKind.ROOT_CAUSE in kinds
    root = next(s for s in sections if s.kind is SectionKind.ROOT_CAUSE)
    assert root.body == "Not determined (insufficient evidence)."


def test_build_sections_meta_section_present_with_duration() -> None:
    ctx: dict[str, Any] = {
        "severity": "low",
        "alert_name": "Quiet",
        "pipeline_name": "noop",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
        "investigation_duration_seconds": 12,
        "alert_id": "alert-1",
    }
    meta = next(s for s in build_sections(ctx) if s.kind is SectionKind.META)
    assert meta.extras["duration_seconds"] == 12
    assert meta.extras["alert_id"] == "alert-1"


# ---------------------------------------------------------------------------
# prepare_sections_for — build + dedupe composition
# ---------------------------------------------------------------------------


def test_prepare_sections_for_drops_redundant_root_cause() -> None:
    ctx: dict[str, Any] = {
        "severity": "critical",
        "alert_name": "PodCrash",
        "pipeline_name": "ingest",
        "root_cause": "PodCrash on ingest (severity: critical)",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    kinds = [s.kind for s in prepare_sections_for(ctx)]
    assert SectionKind.SEVERITY_HEADER in kinds
    assert SectionKind.ROOT_CAUSE not in kinds


def test_prepare_sections_for_keeps_informative_root_cause() -> None:
    ctx: dict[str, Any] = {
        "severity": "critical",
        "alert_name": "PodCrash",
        "pipeline_name": "ingest",
        "root_cause": "PodCrash on ingest because OOMKilled events appeared in the kubelet log",
        "validated_claims": [],
        "non_validated_claims": [],
        "evidence": {},
    }
    kinds = [s.kind for s in prepare_sections_for(ctx)]
    assert SectionKind.SEVERITY_HEADER in kinds
    assert SectionKind.ROOT_CAUSE in kinds
