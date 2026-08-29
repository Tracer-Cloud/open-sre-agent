from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

DEFAULT_CUSTOM_MONITOR_TENANT = "sunfire_biz_juicer"
CUSTOM_MONITOR_URL_RE = re.compile(
    r"https?://[^\s\])]+/custom/(?P<tenant>\d+)/[^\s\])]+?/"
    r"(?P<plugin>[A-Za-z][A-Za-z0-9_-]*)/(?P<plugin_id>\d+)[^\s\])]*",
    re.IGNORECASE,
)
CUSTOM_MONITOR_CODE_RE = re.compile(
    r"\b(?P<tenant>\d+)[_-](?P<plugin>spm|sm|mm|gc|spl|uni|unification|singleMinute|multiMinute|generalComp)[_-](?P<plugin_id>\d+)\b",
    re.IGNORECASE,
)
ALARM_BRACKET_METRIC_RE = re.compile(r"\[([^\[\]]{1,40})\]\([^)]*/custom/[^)]*\)")
CURRENT_VALUE_RE = re.compile(r"当前值为[:：]?\s*([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class CustomMonitorRef:
    """Visible custom-monitor identifier parsed from an alarm."""

    tenant_id: str
    plugin_type: str
    plugin_id: str
    code: str
    source: str
    url_dimensions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CustomMonitorMetricQuery:
    """A PromQL query derived from monitor fields metadata."""

    metric_name: str
    display_name: str
    query: str
    tenant: str
    dimensions: dict[str, str]
    score_hint: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_custom_monitor_ref(alarm: dict[str, Any]) -> CustomMonitorRef | None:
    """Extract a custom monitor reference from visible alarm fields."""

    text = _alarm_text(alarm)
    url_match = CUSTOM_MONITOR_URL_RE.search(text)
    url_dimensions: dict[str, str] = {}
    if url_match:
        url = url_match.group(0)
        parsed = urlsplit(url)
        url_dimensions = {
            unquote(key): unquote(value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key and value
        }
        return _ref(
            url_match.group("tenant"),
            url_match.group("plugin"),
            url_match.group("plugin_id"),
            source="url",
            url_dimensions=url_dimensions,
        )
    code_match = CUSTOM_MONITOR_CODE_RE.search(str(alarm.get("metric") or ""))
    if code_match is None:
        code_match = CUSTOM_MONITOR_CODE_RE.search(text)
    if code_match is None:
        return None
    return _ref(
        code_match.group("tenant"),
        code_match.group("plugin"),
        code_match.group("plugin_id"),
        source="metric",
        url_dimensions={},
    )


def custom_monitor_dimension_values(alarm: dict[str, Any], ref: CustomMonitorRef) -> dict[str, str]:
    """Collect visible dimension filters from alarm tags and custom monitor URLs."""

    values = dict(ref.url_dimensions)
    for group in alarm.get("alarm_tags") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                values.setdefault(name, value)
    return values


def custom_monitor_metric_queries(
    fields: dict[str, Any],
    alarm: dict[str, Any],
    ref: CustomMonitorRef,
    *,
    limit: int = 4,
) -> list[CustomMonitorMetricQuery]:
    """Build metric queries from ``sf monitor fields`` output."""

    metrics = fields.get("metricVOList") if isinstance(fields, dict) else None
    if not isinstance(metrics, list):
        return []
    dimension_values = custom_monitor_dimension_values(alarm, ref)
    ranked: list[tuple[float, CustomMonitorMetricQuery]] = []
    for raw_metric in metrics:
        if not isinstance(raw_metric, dict):
            continue
        metric_name = str(raw_metric.get("name") or "").strip()
        display_name = str(raw_metric.get("displayName") or "").strip()
        if not metric_name:
            continue
        dimensions = _dimensions_for_metric(raw_metric, dimension_values)
        selector = _selector(dimensions)
        aggregator = _aggregator_for_metric(raw_metric)
        group_by = f" by ({','.join(dimensions)})" if dimensions else ""
        query = f"{aggregator}({metric_name}{selector}){group_by}"
        score = _metric_relevance_score(display_name, alarm)
        ranked.append(
            (
                score,
                CustomMonitorMetricQuery(
                    metric_name=metric_name,
                    display_name=display_name or metric_name,
                    query=query,
                    tenant=DEFAULT_CUSTOM_MONITOR_TENANT,
                    dimensions=dimensions,
                    score_hint=round(score, 3),
                ),
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1].metric_name))
    return [item for _score, item in ranked[:limit]]


def summarize_custom_monitor_fields(fields: dict[str, Any], ref: CustomMonitorRef) -> str:
    """Compact ``sf monitor fields`` metadata for evidence bundles."""

    metrics = fields.get("metricVOList") if isinstance(fields, dict) else None
    metric_parts: list[str] = []
    dimensions: set[str] = set()
    if isinstance(metrics, list):
        for item in metrics[:8]:
            if not isinstance(item, dict):
                continue
            display = str(item.get("displayName") or "")
            name = str(item.get("name") or "")
            if display or name:
                metric_parts.append(f"{display}:{name}".strip(":"))
            for dim in item.get("dimensions") or []:
                dimensions.add(str(dim))
    agg_views = fields.get("aggViews") if isinstance(fields, dict) else None
    views = (
        [
            str(item.get("dim") or "")
            for item in agg_views[:8]
            if isinstance(item, dict) and item.get("dim")
        ]
        if isinstance(agg_views, list)
        else []
    )
    return _clip(
        " ".join(
            part
            for part in (
                f"custom_monitor code={ref.code}",
                f"name={fields.get('name') or ''}",
                f"metrics={metric_parts}",
                f"dimensions={sorted(dimensions)}",
                f"agg_views={views}",
            )
            if part
        )
    )


def summarize_custom_monitor_spec(spec: dict[str, Any], ref: CustomMonitorRef) -> str:
    """Compact ``sf monitor get`` metadata into root-cause oriented text."""

    log = spec.get("log") if isinstance(spec.get("log"), dict) else {}
    spm = spec.get("spm") if isinstance(spec.get("spm"), dict) else {}
    group_by = _dim_names(spec.get("groupBy"))
    white_filters = _filter_summaries(spec.get("whiteFilters"))
    black_filters = _filter_summaries(spec.get("blackFilters"))
    result_dim = _dim_name(spm.get("resultDim"))
    cost_dim = _dim_name(spm.get("costDim"))
    return _clip(
        " ".join(
            part
            for part in (
                f"custom_monitor_spec code={ref.code}",
                f"name={spec.get('name') or ''}",
                f"source={spec.get('sourceType') or ''}",
                f"log_path={log.get('path') or ''}",
                f"apps={log.get('apps') or []}",
                f"group_by={group_by}",
                f"result_dim={result_dim}",
                f"cost_dim={cost_dim}",
                f"white_filters={white_filters}",
                f"black_filters={black_filters}",
            )
            if part
        ),
        1000,
    )


def custom_monitor_signal_score(
    query: CustomMonitorMetricQuery,
    labels: dict[str, Any],
    summary: dict[str, Any],
) -> float:
    """Score one custom monitor metric series for graph root ranking."""

    display = query.display_name.lower()
    maximum = _float(summary.get("max"))
    minimum = _float(summary.get("min"))
    average = _float(summary.get("avg"))
    trend = str(summary.get("trend") or "").lower()
    score = 3.0 + min(query.score_hint, 1.2)
    if any(marker in display for marker in ("失败", "error", "fail")) and maximum is not None:
        score += min(1.4, max(0.0, maximum) / 30.0)
        if trend == "rising":
            score += 0.35
    elif any(marker in display for marker in ("成功率", "success")):
        low = min([value for value in (minimum, average) if value is not None], default=None)
        if low is not None:
            score += min(1.4, max(0.0, 100.0 - low) / 20.0 if low > 1.0 else (1.0 - low) * 4.0)
        if trend == "falling":
            score += 0.35
    elif any(marker in display for marker in ("耗时", "rt", "latency")) and maximum is not None:
        score += min(1.2, max(0.0, maximum) / 3000.0)
        if trend == "rising":
            score += 0.25
    if labels:
        score += 0.15
    return round(min(score, 5.0), 3)


def custom_monitor_signal_label(
    ref: CustomMonitorRef,
    query: CustomMonitorMetricQuery,
    labels: dict[str, Any],
) -> str:
    """Build a stable label for a custom monitor metric series."""

    label_parts = []
    for key, value in labels.items():
        if key == "__name__" or value in (None, ""):
            continue
        label_parts.append(f"{key}={value}")
    suffix = ",".join(label_parts[:3]) if label_parts else ref.code
    return f"{ref.code}:{query.display_name}:{suffix}"


def triggered_metric_names(alarm: dict[str, Any]) -> list[str]:
    """Return metric display names explicitly mentioned by custom alarm markdown."""

    text = _alarm_text(alarm)
    names = [match.strip() for match in ALARM_BRACKET_METRIC_RE.findall(text)]
    return _unique(names)


def _ref(
    tenant_id: str,
    plugin_type: str,
    plugin_id: str,
    *,
    source: str,
    url_dimensions: dict[str, str],
) -> CustomMonitorRef:
    normalized_plugin = plugin_type.upper()
    return CustomMonitorRef(
        tenant_id=tenant_id,
        plugin_type=plugin_type,
        plugin_id=plugin_id,
        code=f"{tenant_id}_{normalized_plugin}_{plugin_id}",
        source=source,
        url_dimensions=url_dimensions,
    )


def _alarm_text(alarm: dict[str, Any]) -> str:
    return " ".join(
        str(alarm.get(key) or "") for key in ("metric", "monitor_item_name", "title", "content")
    )


def _dimensions_for_metric(raw_metric: dict[str, Any], values: dict[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for raw_dim in raw_metric.get("dimensions") or []:
        dim = str(raw_dim)
        value = values.get(dim)
        if value:
            output[dim] = value
    return output


def _selector(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in labels.items()]
    return "{" + ",".join(parts) + "}"


def _aggregator_for_metric(raw_metric: dict[str, Any]) -> str:
    display = str(raw_metric.get("displayName") or "").lower()
    configured = str(raw_metric.get("spaceAggregator") or "").lower()
    if "成功率" in display or "success" in display:
        return "min"
    if configured in {"sum", "avg", "min", "max"}:
        return configured
    if any(marker in display for marker in ("量", "数", "count", "qps")):
        return "sum"
    return "avg"


def _metric_relevance_score(display_name: str, alarm: dict[str, Any]) -> float:
    text = _alarm_text(alarm).lower()
    display = display_name.lower()
    score = 0.0
    for explicit in triggered_metric_names(alarm):
        if explicit and explicit.lower() == display:
            score += 1.5
    if any(marker in display for marker in ("失败", "fail", "error")) and any(
        marker in text for marker in ("失败", "fail", "error", "异常")
    ):
        score += 1.0
    if "成功率" in display and "成功率" in text:
        score += 1.0
    if any(marker in display for marker in ("耗时", "rt", "latency")) and any(
        marker in text for marker in ("耗时", "rt", "延迟")
    ):
        score += 0.8
    if any(marker in display for marker in ("总量", "成功量")):
        score -= 0.2
    return score


def _dim_names(value: Any) -> list[str]:
    names = []
    for item in value if isinstance(value, list) else []:
        name = _dim_name(item.get("dim") if isinstance(item, dict) else item)
        if name:
            names.append(name)
    return _unique(names)


def _dim_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return ""


def _filter_summaries(value: Any) -> list[str]:
    output = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        dim = _dim_name(item.get("dim"))
        values = [str(raw) for raw in item.get("values") or []][:4]
        if dim or values:
            output.append(f"{dim}={values}")
    return output[:4]


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _clip(value: Any, limit: int = 700) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."
