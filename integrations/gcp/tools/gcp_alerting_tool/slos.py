"""``Service`` and ``ServiceLevelObjective`` normalization.

Everything here is the objective's **definition**. Current error-budget burn is
a separate Cloud Monitoring time series, which is why ``resource_name`` is
returned verbatim: it is the identifier a follow-up ``gcp_monitoring_query``
needs, and returning it is cheaper and more honest than approximating burn.
"""

from __future__ import annotations

from typing import Any

#: ``Service`` oneof field names, in discovery-document order. Exactly one is
#: set on any given service; the reported ``service_kind`` is the snake-cased
#: field name, so a type Google adds later degrades to ``""`` rather than to a
#: wrong label.
_SERVICE_KINDS: tuple[str, ...] = (
    "appEngine",
    "basicService",
    "cloudEndpoints",
    "cloudRun",
    "clusterIstio",
    "custom",
    "gkeNamespace",
    "gkeService",
    "gkeWorkload",
    "istioCanonicalService",
    "meshIstio",
)

#: Position of the service id and of the objective id inside
#: ``projects/{p}/services/{s}/serviceLevelObjectives/{o}``.
_SERVICE_SEGMENT = 3
_OBJECTIVE_SEGMENT = 5

_ANY = "any"


def _snake(camel: str) -> str:
    """Convert a lowerCamelCase discovery field name to snake_case."""
    out: list[str] = []
    for char in camel:
        if char.isupper():
            out.append("_")
            out.append(char.lower())
        else:
            out.append(char)
    return "".join(out)


def _segment(resource_name: str, index: int) -> str:
    """Return path segment ``index``, or ``""`` when the name is shorter."""
    parts = [part for part in resource_name.split("/") if part]
    return parts[index] if len(parts) > index else ""


def _joined(values: Any) -> str:
    """Render a repeated string field as a comma list, ``any`` when empty."""
    if not isinstance(values, list):
        return _ANY
    rendered = [str(value) for value in values if str(value)]
    return ", ".join(rendered) if rendered else _ANY


def service_kind(service: dict[str, Any]) -> str:
    """Return the snake-cased name of whichever ``Service`` oneof field is set."""
    for field in _SERVICE_KINDS:
        if field in service:
            return _snake(field)
    return ""


def normalize_service(service: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``Service`` into the fields each of its SLOs carries."""
    return {
        "resource_name": str(service.get("name", "") or ""),
        "display_name": str(service.get("displayName", "") or ""),
        "kind": service_kind(service),
    }


def _basic_summary(basic: dict[str, Any]) -> tuple[str, str]:
    """Return ``(sli_kind, sli_summary)`` for a ``BasicSli``.

    ``method``, ``location`` and ``version`` sit on the ``BasicSli`` itself, not
    inside ``availability`` — ``AvailabilityCriteria`` has no fields at all.
    """
    methods = _joined(basic.get("method"))
    locations = _joined(basic.get("location"))

    if "latency" in basic:
        latency = basic.get("latency") or {}
        threshold = str(latency.get("threshold", "") or "")
        return (
            "basic_latency",
            f"latency <= {threshold}, methods: {methods}, locations: {locations}",
        )

    return "basic_availability", f"availability, methods: {methods}, locations: {locations}"


def _request_summary(request_based: dict[str, Any]) -> tuple[str, str]:
    """Return ``(sli_kind, sli_summary)`` for a ``RequestBasedSli``."""
    if "distributionCut" in request_based:
        cut = request_based.get("distributionCut") or {}
        bounds = cut.get("range") or {}
        return "request_based_distribution_cut", (
            f"distribution cut on {cut.get('distributionFilter', '')}, "
            f"range {bounds.get('min', '')}..{bounds.get('max', '')}"
        )

    ratio = request_based.get("goodTotalRatio") or {}
    good = str(ratio.get("goodServiceFilter", "") or ratio.get("badServiceFilter", "") or "")
    return "request_based_good_total_ratio", (
        f"good/total ratio, good: {good}, total: {ratio.get('totalServiceFilter', '')}"
    )


def describe_sli(sli: dict[str, Any]) -> tuple[str, str]:
    """Return ``(sli_kind, sli_summary)`` for a ``ServiceLevelIndicator``."""
    basic = sli.get("basicSli")
    if isinstance(basic, dict):
        return _basic_summary(basic)

    request_based = sli.get("requestBased")
    if isinstance(request_based, dict):
        return _request_summary(request_based)

    windows = sli.get("windowsBased")
    if isinstance(windows, dict):
        period = str(windows.get("windowPeriod", "") or "")
        return "windows_based", f"windows-based, window {period}" if period else "windows-based"

    return "unknown", ""


def objective_period(slo: dict[str, Any]) -> str:
    """Render the SLO period.

    ``rollingPeriod`` and ``calendarPeriod`` are top-level oneof fields on the
    objective, not members of a ``period`` sub-object.
    """
    rolling = str(slo.get("rollingPeriod", "") or "")
    if rolling:
        return f"rolling {rolling}"
    calendar = str(slo.get("calendarPeriod", "") or "")
    if calendar:
        return f"calendar {calendar}"
    return ""


def normalize_slo(slo: dict[str, Any], service: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ``ServiceLevelObjective`` against its normalized service."""
    resource_name = str(slo.get("name", "") or "")
    sli_kind, sli_summary = describe_sli(slo.get("serviceLevelIndicator") or {})

    return {
        "service": _segment(resource_name, _SERVICE_SEGMENT),
        "service_display_name": str(service.get("display_name", "") or ""),
        "service_kind": str(service.get("kind", "") or ""),
        "id": _segment(resource_name, _OBJECTIVE_SEGMENT),
        "display_name": str(slo.get("displayName", "") or ""),
        "resource_name": resource_name,
        # A plain double on the objective, not a wrapped value message.
        "goal": slo.get("goal", 0.0),
        "period": objective_period(slo),
        "sli_kind": sli_kind,
        "sli_summary": sli_summary,
        "user_labels": slo.get("userLabels") or {},
    }


def keep_for_name(slo: dict[str, Any], needle: str) -> bool:
    """Case-insensitive substring match on the SLO or service display name."""
    if not needle:
        return True
    folded = needle.casefold()
    return folded in str(slo.get("display_name", "") or "").casefold() or (
        folded in str(slo.get("service_display_name", "") or "").casefold()
    )
