"""Channel-agnostic section model for the RCA report.

A :class:`Section` is the unit of report content. The pipeline is::

    build_sections(ctx)        -> list[Section]    # pure extraction
    dedupe_sections(sections)  -> list[Section]    # cross-section noise filter
    prepare_sections_for(ctx) = dedupe_sections(build_sections(ctx))

Each per-channel renderer (Slack mrkdwn, Slack Block Kit, Telegram HTML,
Discord markdown) consumes the prepared list and emits its own dialect. The
split keeps parity by construction: a new section appears in every channel
that knows how to render its ``kind``, and a new channel only needs to
implement the ``kind`` switch.

Section ordering mirrors ``format_telegram_message`` — that is the parity
target until renderers are reorganized in phase B of issue/2007. Several
derivation helpers (``_derive_root_cause_sentence``, ``_get_top_error_log``,
``_format_provenance_lines``, ``_resolve_evidence_tags``) still live in
``report.py`` and are imported lazily here; the phase-B refactor collapses
the duplication by moving those helpers into this module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from app.delivery.publish_findings.formatters.infrastructure import (
    build_investigation_trace,
    get_failed_pods,
)
from app.delivery.publish_findings.report_context import ReportContext


class SectionKind(StrEnum):
    """Discriminator for :class:`Section` payloads."""

    SEVERITY_HEADER = "severity_header"
    ROOT_CAUSE = "root_cause"
    FAILED_PODS = "failed_pods"
    CLAIMS = "claims"
    UPSTREAM_CORRELATION = "upstream_correlation"
    PROVENANCE = "provenance"
    REMEDIATION = "remediation"
    TRACE = "trace"
    EVIDENCE = "evidence"
    LINK = "link"
    META = "meta"


@dataclass(frozen=True)
class Section:
    """A channel-agnostic chunk of report content.

    Fields are intentionally permissive — not every kind uses every field.
    Per-kind contracts:

    - ``SEVERITY_HEADER`` — ``extras`` has ``severity``, ``alert_name``, ``pipeline_name``.
    - ``ROOT_CAUSE``      — ``body`` is the derived sentence (may be None when dedup
                             cleared it); ``extras["top_log"]`` is an optional plain-text
                             error log to render as a code block under the sentence.
    - ``FAILED_PODS``     — ``extras["pods"]`` is a tuple of pod dicts; ``extras["datadog_site"]``.
    - ``CLAIMS``          — ``items`` are claim texts; ``extras["validated"]`` (bool);
                             ``extras["evidence_refs"]`` is a parallel tuple — one tuple of
                             ``{"display_id": str, "url": str | None}`` per item.
    - ``PROVENANCE``      — ``items`` are ``"Label: summary"`` bullets.
    - ``REMEDIATION``     — ``items`` are remediation step strings.
    - ``TRACE``           — ``items`` are investigation trace lines.
    - ``EVIDENCE``        — ``extras["catalog"]`` is a filtered evidence-id → entry mapping.
    - ``LINK``            — ``extras["url"]``, ``extras["label"]``; optional ``log_group`` /
                             ``log_stream`` when no console URL is available.
    - ``META``            — ``extras["duration_seconds"]``, ``extras["alert_id"]``.
    """

    kind: SectionKind
    title: str | None = None
    body: str | None = None
    items: tuple[str, ...] = ()
    extras: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Section builders — one per kind, each pure over ``ctx``
# ---------------------------------------------------------------------------


def _build_severity_header(ctx: ReportContext) -> Section | None:
    severity = str(ctx.get("severity") or "").strip()
    alert = str(ctx.get("alert_name") or "").strip()
    pipeline = str(ctx.get("pipeline_name") or "").strip()
    if not alert or not pipeline:
        return None
    return Section(
        kind=SectionKind.SEVERITY_HEADER,
        extras={"severity": severity, "alert_name": alert, "pipeline_name": pipeline},
    )


def _build_root_cause(ctx: ReportContext) -> Section | None:
    # Lazy import to avoid a load-order cycle with report.py until phase B
    # promotes these helpers into this module.
    from app.delivery.publish_findings.formatters.report import (
        _derive_root_cause_sentence,
        _get_top_error_log,
    )

    sentence = _derive_root_cause_sentence(ctx)
    top_log = _get_top_error_log(ctx.get("evidence") or {})

    # Always emit a ROOT_CAUSE section so callers see *something* even when
    # the investigation produced no derived sentence and no error logs.
    # The legacy per-formatter code always rendered this fallback; dropping
    # the section here would silently hide that signal from all channels.
    # ``dedupe_sections._root_cause_repeats_header`` returns False for the
    # "Not determined" prefix, so the fallback is never deduped away.
    body = sentence or ("Not determined (insufficient evidence)." if not top_log else None)
    extras: dict[str, Any] = {"top_log": top_log} if top_log else {}
    return Section(kind=SectionKind.ROOT_CAUSE, body=body, extras=extras)


def _build_failed_pods(ctx: ReportContext) -> Section | None:
    pods = get_failed_pods(ctx)
    if not pods:
        return None
    return Section(
        kind=SectionKind.FAILED_PODS,
        title="Failed Pods",
        extras={
            "pods": tuple(pods),
            "datadog_site": ctx.get("datadog_site", "datadoghq.com"),
        },
    )


def _build_claims(ctx: ReportContext) -> tuple[Section | None, Section | None]:
    from app.delivery.publish_findings.formatters.report import _resolve_evidence_tags

    catalog = ctx.get("evidence_catalog") or {}
    evidence = ctx.get("evidence") or {}

    def _claim_text(claim_data: Mapping[str, Any]) -> str:
        raw = claim_data.get("claim", "") or ""
        return _resolve_evidence_tags(raw, evidence)

    def _refs_for(claim_data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        evidence_ids = claim_data.get("evidence_ids") or []
        evidence_labels = claim_data.get("evidence_labels") or []
        if evidence_ids:
            refs = [
                {
                    "display_id": (catalog.get(eid) or {}).get("display_id", eid),
                    "url": (catalog.get(eid) or {}).get("url"),
                }
                for eid in evidence_ids
            ]
            return tuple(refs)
        if evidence_labels:
            return tuple({"display_id": str(label), "url": None} for label in evidence_labels)
        return ()

    validated_claims = ctx.get("validated_claims") or []
    non_validated_claims = ctx.get("non_validated_claims") or []

    validated_section: Section | None = None
    if validated_claims:
        items = tuple(_claim_text(c) for c in validated_claims)
        refs = tuple(_refs_for(c) for c in validated_claims)
        validated_section = Section(
            kind=SectionKind.CLAIMS,
            title="Findings",
            items=items,
            extras={"validated": True, "evidence_refs": refs},
        )

    non_validated_section: Section | None = None
    if non_validated_claims:
        items = tuple(_claim_text(c) for c in non_validated_claims)
        non_validated_section = Section(
            kind=SectionKind.CLAIMS,
            title="Non-Validated Claims (Inferred)",
            items=items,
            extras={"validated": False, "evidence_refs": tuple(() for _ in items)},
        )

    return validated_section, non_validated_section


def _build_provenance(ctx: ReportContext) -> Section | None:
    from app.delivery.publish_findings.formatters.report import _format_provenance_lines

    lines = _format_provenance_lines(ctx)
    if not lines:
        return None
    # _format_provenance_lines prefixes "• " — strip so renderers control bullets.
    items = tuple(line.lstrip("• ").strip() for line in lines)
    return Section(kind=SectionKind.PROVENANCE, title="Provenance", items=items)


def _build_correlation(ctx: ReportContext) -> Section | None:
    """Build the UPSTREAM_CORRELATION section from ``ctx["correlation"]``.

    Reuses ``_format_correlation_lines`` (PR #1877) to produce the
    pre-bulleted signal and driver strings, then stashes them in
    ``extras`` so renderers can wrap them in channel-native sub-headings.
    """
    from app.delivery.publish_findings.formatters.report import _format_correlation_lines

    signal_lines, driver_lines = _format_correlation_lines(ctx)
    if not signal_lines and not driver_lines:
        return None
    # Strip "• " prefixes so renderers can choose their own bullet glyph.
    signals = tuple(line.lstrip("• ").strip() for line in signal_lines)
    drivers = tuple(line.lstrip("• ").strip() for line in driver_lines)
    return Section(
        kind=SectionKind.UPSTREAM_CORRELATION,
        title="Upstream Correlation",
        extras={"signals": signals, "drivers": drivers},
    )


def _build_remediation(ctx: ReportContext) -> Section | None:
    steps = ctx.get("remediation_steps") or []
    if not steps:
        return None
    return Section(
        kind=SectionKind.REMEDIATION,
        title="Recommended Actions",
        items=tuple(str(s) for s in steps),
    )


def _build_trace(ctx: ReportContext) -> Section | None:
    steps = build_investigation_trace(ctx)
    if not steps:
        return None
    return Section(kind=SectionKind.TRACE, title="Investigation Trace", items=tuple(steps))


def _build_evidence(ctx: ReportContext) -> Section | None:
    catalog = ctx.get("evidence_catalog") or {}
    # Per-pod entries are surfaced by FAILED_PODS — skip them here, matching
    # the existing format_cited_evidence_section behaviour.
    filtered = {
        eid: entry
        for eid, entry in catalog.items()
        if not eid.startswith("evidence/datadog/failed_pod/")
    }
    if not filtered:
        return None
    return Section(
        kind=SectionKind.EVIDENCE,
        title="Cited Evidence",
        extras={"catalog": filtered},
    )


def _build_cloudwatch_link(ctx: ReportContext) -> Section | None:
    from app.delivery.publish_findings.urls.aws import build_cloudwatch_url

    url = ctx.get("cloudwatch_logs_url")
    if not url:
        group = ctx.get("cloudwatch_log_group")
        stream = ctx.get("cloudwatch_log_stream")
        if not (group and stream):
            return None
        url = build_cloudwatch_url(ctx)
        if not url:
            return Section(
                kind=SectionKind.LINK,
                title="CloudWatch Logs",
                extras={
                    "url": None,
                    "label": "View logs",
                    "log_group": group,
                    "log_stream": stream,
                },
            )
    return Section(
        kind=SectionKind.LINK,
        title="CloudWatch",
        extras={"url": str(url), "label": "View logs"},
    )


def _build_meta(ctx: ReportContext) -> Section | None:
    duration = ctx.get("investigation_duration_seconds")
    alert_id = ctx.get("alert_id")
    if duration is None and not alert_id:
        return None
    return Section(
        kind=SectionKind.META,
        extras={"duration_seconds": duration, "alert_id": alert_id},
    )


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------


def build_sections(ctx: ReportContext) -> list[Section]:
    """Build the channel-agnostic, ordered section list for ``ctx``.

    Order mirrors ``format_telegram_message`` (the parity target). Sections
    that would render empty are omitted here so renderers never have to
    second-guess.
    """
    validated_claims, non_validated_claims = _build_claims(ctx)
    candidates: tuple[Section | None, ...] = (
        _build_severity_header(ctx),
        _build_root_cause(ctx),
        _build_failed_pods(ctx),
        validated_claims,
        non_validated_claims,
        _build_correlation(ctx),
        _build_provenance(ctx),
        _build_remediation(ctx),
        _build_trace(ctx),
        _build_evidence(ctx),
        _build_cloudwatch_link(ctx),
        _build_meta(ctx),
    )
    return [section for section in candidates if section is not None]


# ---------------------------------------------------------------------------
# Noise filters — pure passes over list[Section]
# ---------------------------------------------------------------------------


def _norm_banner_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _root_cause_repeats_header(header: Section, root_cause_body: str) -> bool:
    """Port of ``report._telegram_baseline_repeats_header``, lifted to sections.

    True when the root-cause sentence just restates the alert + pipeline that
    the severity header already shows. Excludes obvious causal sentences
    (because / due to / caused) and long, content-rich sentences (> 220 chars).
    Preserves the "Not determined (insufficient evidence)." fallback so users
    can distinguish "we didn't find a cause" from "we hid the cause."
    """
    alert = str(header.extras.get("alert_name") or "").strip()
    pipeline = str(header.extras.get("pipeline_name") or "").strip()
    if not alert or not pipeline:
        return False
    sentence = root_cause_body.strip()
    if not sentence or sentence.startswith("Not determined"):
        return False
    if len(sentence) > 220:
        return False
    rc = _norm_banner_key(sentence)
    if _norm_banner_key(alert) not in rc or _norm_banner_key(pipeline) not in rc:
        return False
    if "because" in rc or "due to" in rc or "caused" in rc:
        return False
    # Explicit "severity:" marker is a strong banner signal regardless of length.
    if "severity" in rc:
        return True
    return len(sentence) < 120


def dedupe_sections(sections: list[Section]) -> list[Section]:
    """Apply cross-section noise filters.

    Currently a single pass:

    - When the SEVERITY_HEADER already shows the alert + pipeline and the
      ROOT_CAUSE body just restates that pair (no causal language, short
      sentence), drop the ROOT_CAUSE section. If it carries a ``top_log``
      extra, keep the section with ``body=None`` so the log still renders
      as a code snippet under the header.

    Pure function over the section list. New filters land here as additional
    passes — keep them composable and side-effect free.
    """
    header = next((s for s in sections if s.kind is SectionKind.SEVERITY_HEADER), None)
    if header is None:
        return list(sections)

    result: list[Section] = []
    for section in sections:
        if section.kind is SectionKind.ROOT_CAUSE and _root_cause_repeats_header(
            header, section.body or ""
        ):
            if section.extras.get("top_log"):
                result.append(replace(section, body=None))
            # else: drop entirely — header alone is enough
            continue
        result.append(section)
    return result


def prepare_sections_for(ctx: ReportContext) -> list[Section]:
    """Canonical pipeline: build → dedupe.

    Renderers call this rather than ``build_sections`` directly so the dedup
    pass (and any future passes) can never be silently skipped.
    """
    return dedupe_sections(build_sections(ctx))
