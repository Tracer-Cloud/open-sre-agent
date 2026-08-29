from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from tests.benchmarks.realrca_graph.features import clip_text

SQL_TABLE_RE = re.compile(
    r"\b(?:from|join|update|insert\s+into|delete\s+from)\s+`?([a-zA-Z0-9_.$-]{2,80})`?",
    re.IGNORECASE,
)
SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class RdsSqlSignal:
    """Compact root-cause signal extracted from sf diagnose rds-sql output."""

    label: str
    score: float
    reason: str
    summary: str
    props: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_rds_sql(records: Any) -> str:
    """Summarize RDS SQL diagnose rows without retaining full SQL bodies."""

    row_count = len(_result_rows(records))
    signals = rds_sql_signals(records)
    if not signals:
        return f"rds_sql count={row_count} top="
    top = [signal.summary for signal in signals[:3]]
    return f"rds_sql count={row_count} top={top}"


def rds_sql_signals(records: Any) -> list[RdsSqlSignal]:
    """Extract ranked SQL signals from matrix stats and stream detail rows."""

    signals = [*rds_sql_stat_signals(records), *rds_sql_detail_signals(records)]
    signals.sort(
        key=lambda item: (
            -item.score,
            -float(item.props.get("total_time") or 0.0),
            -float(item.props.get("avg_cost") or item.props.get("cost") or 0.0),
            item.label,
        )
    )
    return signals


def rds_sql_stat_signals(records: Any) -> list[RdsSqlSignal]:
    """Aggregate matrix metric rows into per-sql-id statistical signals."""

    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in _result_rows(records):
        metric = row.get("metric") if isinstance(row.get("metric"), dict) else {}
        if not metric:
            continue
        metric_name = str(metric.get("__name__") or "").lower()
        sql_id = str(metric.get("sql_id") or metric.get("sqlId") or "").strip()
        template = str(metric.get("sql_text_template") or metric.get("sqlTextTemplate") or "")
        db = str(metric.get("db") or "").strip()
        instance = str(metric.get("instance_name") or metric.get("instance") or "").strip()
        if not sql_id and not template:
            continue
        key = (instance, db, sql_id, template)
        bucket = buckets.setdefault(
            key,
            {
                "instance_id": instance,
                "db": db,
                "sql_id": sql_id,
                "sql_text_template": template,
                "metrics": {},
            },
        )
        value = _max_numeric(row.get("values"))
        if value is not None and metric_name:
            bucket["metrics"][_canonical_metric(metric_name)] = value

    signals: list[RdsSqlSignal] = []
    for bucket in buckets.values():
        metrics = bucket["metrics"]
        table = _sql_table(str(bucket.get("sql_text_template") or ""))
        sql_id = str(bucket.get("sql_id") or "")
        synthetic_load = _is_synthetic_sql(bucket)
        avg_cost = _metric_value(metrics, "avg_cost")
        total_time = _metric_value(metrics, "total_time")
        execute_count = _metric_value(metrics, "execute_count")
        examined_rows = _metric_value(metrics, "examined_rows")
        label = _signal_label(table, sql_id, bucket)
        score = _stat_score(avg_cost, total_time, execute_count, examined_rows, synthetic_load)
        reason = (
            "RDS SQL diagnose reports synthetic load SQL near alarm"
            if synthetic_load
            else "RDS SQL diagnose reports high-cost SQL near alarm"
        )
        summary = _summary(
            kind="stat",
            instance=str(bucket.get("instance_id") or ""),
            db=str(bucket.get("db") or ""),
            sql_id=sql_id,
            table=table,
            avg_cost=avg_cost,
            total_time=total_time,
            execute_count=execute_count,
            examined_rows=examined_rows,
            synthetic_load=synthetic_load,
        )
        signals.append(
            RdsSqlSignal(
                label=label,
                score=score,
                reason=reason,
                summary=summary,
                props={
                    "instance_id": bucket.get("instance_id") or "",
                    "db": bucket.get("db") or "",
                    "sql_id": sql_id,
                    "sql_table": table,
                    "sql_text_template": clip_text(bucket.get("sql_text_template") or "", 500),
                    "avg_cost": avg_cost,
                    "total_time": total_time,
                    "execute_count": execute_count,
                    "examined_rows": examined_rows,
                    "synthetic_load": synthetic_load,
                    "signal_family": "rds_sql_stat",
                },
            )
        )
    return signals


def rds_sql_detail_signals(records: Any) -> list[RdsSqlSignal]:
    """Extract per-row SQL execution detail signals from stream rows."""

    buckets: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _result_rows(records):
        stream = row.get("stream") if isinstance(row.get("stream"), dict) else {}
        for _timestamp, value in _iter_values(row.get("values")):
            payload = _json_value(value)
            if not isinstance(payload, dict):
                continue
            merged = {**stream, **payload}
            sql_text = str(
                merged.get("sql_text") or merged.get("sql") or merged.get("sqlText") or ""
            )
            sql_id = str(merged.get("sql_id") or merged.get("sqlId") or stream.get("sql_id") or "")
            db = str(merged.get("db") or stream.get("db") or "")
            instance = str(
                merged.get("instance_name")
                or merged.get("instance_id")
                or stream.get("instance_name")
                or ""
            )
            user = str(merged.get("user") or merged.get("user_name") or "")
            table = _sql_table(sql_text)
            if not any((sql_text, sql_id, table)):
                continue
            buckets[(instance, db, sql_id, table, user)].append(merged)

    signals: list[RdsSqlSignal] = []
    for (instance, db, sql_id, table, user), items in buckets.items():
        costs = [_to_float(item.get("cost")) for item in items]
        locks = [_to_float(item.get("lock_wait_time")) for item in items]
        examined = [_to_float(item.get("examined_row_count")) for item in items]
        max_cost = max((value for value in costs if value is not None), default=0.0)
        max_lock = max((value for value in locks if value is not None), default=0.0)
        max_examined = max((value for value in examined if value is not None), default=0.0)
        sql_text = str(
            items[0].get("sql_text") or items[0].get("sql") or items[0].get("sqlText") or ""
        )
        synthetic_load = _is_synthetic_sql({"sql_text_template": sql_text, "user": user})
        label = _signal_label(table, sql_id, {"db": db, "instance_id": instance})
        score = _detail_score(max_cost, max_lock, max_examined, len(items), synthetic_load)
        summary = _summary(
            kind="detail",
            instance=instance,
            db=db,
            sql_id=sql_id,
            table=table,
            cost=max_cost,
            lock_wait=max_lock,
            examined_rows=max_examined,
            execute_count=float(len(items)),
            synthetic_load=synthetic_load,
            user=user,
        )
        signals.append(
            RdsSqlSignal(
                label=label,
                score=score,
                reason=(
                    "RDS SQL detail reports synthetic load execution near alarm"
                    if synthetic_load
                    else "RDS SQL detail reports slow or locked execution near alarm"
                ),
                summary=summary,
                props={
                    "instance_id": instance,
                    "db": db,
                    "sql_id": sql_id,
                    "sql_table": table,
                    "user": user,
                    "sql_text": clip_text(sql_text, 500),
                    "cost": max_cost,
                    "lock_wait_time": max_lock,
                    "examined_rows": max_examined,
                    "execute_count": len(items),
                    "synthetic_load": synthetic_load,
                    "signal_family": "rds_sql_detail",
                },
            )
        )
    return signals


def _result_rows(records: Any) -> list[dict[str, Any]]:
    if isinstance(records, dict):
        rows = (
            records.get("result")
            or records.get("rows")
            or records.get("data")
            or records.get("items")
            or []
        )
    elif isinstance(records, list):
        rows = records
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _iter_values(values: Any) -> list[tuple[Any, Any]]:
    if not isinstance(values, list):
        return []
    output: list[tuple[Any, Any]] = []
    for item in values:
        if isinstance(item, list) and len(item) >= 2 or isinstance(item, tuple) and len(item) >= 2:
            output.append((item[0], item[1]))
    return output


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _max_numeric(values: Any) -> float | None:
    parsed = [_to_float(value) for _timestamp, value in _iter_values(values)]
    concrete = [value for value in parsed if value is not None]
    if not concrete:
        return None
    return max(concrete)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_metric(name: str) -> str:
    compact = name.replace(" ", "")
    if "avg(cost)" in compact:
        return "avg_cost"
    if "sum(sql_id_total_time)" in compact or "total_time" in compact:
        return "total_time"
    if "sum(execute_count)" in compact or "execute_count" in compact:
        return "execute_count"
    if "examined_row_count" in compact:
        return "examined_rows"
    if "lock_wait_time" in compact:
        return "lock_wait_time"
    return compact


def _metric_value(metrics: dict[str, float], key: str) -> float:
    return float(metrics.get(key) or 0.0)


def _sql_table(text: str) -> str:
    cleaned = SQL_BLOCK_COMMENT_RE.sub(" ", text)
    match = SQL_TABLE_RE.search(cleaned)
    return match.group(1).strip("`").lower() if match else ""


def _signal_label(table: str, sql_id: str, bucket: dict[str, Any]) -> str:
    parts = [part for part in (table, sql_id) if part]
    if not parts:
        parts = [str(bucket.get("db") or bucket.get("instance_id") or "rds_sql")]
    return " ".join([*parts, "slow_sql"])


def _stat_score(
    avg_cost: float,
    total_time: float,
    execute_count: float,
    examined_rows: float,
    synthetic_load: bool,
) -> float:
    score = 4.1
    if avg_cost >= 1_000.0:
        score += 0.25
    if avg_cost >= 100_000.0:
        score += 0.25
    if total_time >= 1_000_000.0:
        score += 0.25
    if execute_count >= 10.0:
        score += 0.15
    if examined_rows >= 10_000.0:
        score += 0.2
    if synthetic_load:
        score = min(score, 4.25)
    return round(min(score, 5.0), 3)


def _detail_score(
    cost: float,
    lock_wait: float,
    examined_rows: float,
    count: int,
    synthetic_load: bool,
) -> float:
    score = 4.0
    if cost >= 1_000.0:
        score += 0.25
    if cost >= 10_000.0:
        score += 0.25
    if lock_wait >= 1_000.0:
        score += 0.25
    if examined_rows >= 10_000.0:
        score += 0.2
    if count >= 2:
        score += 0.15
    if synthetic_load:
        score = min(score, 4.2)
    return round(min(score, 5.0), 3)


def _summary(kind: str, **values: Any) -> str:
    ordered = [
        "instance",
        "db",
        "sql_id",
        "table",
        "avg_cost",
        "cost",
        "total_time",
        "execute_count",
        "examined_rows",
        "lock_wait",
        "user",
        "synthetic_load",
    ]
    parts = [f"kind={kind}"]
    for key in ordered:
        value = values.get(key)
        if value not in (None, "", 0, 0.0, False):
            parts.append(f"{key}={_fmt(value)}")
    if values.get("synthetic_load"):
        parts.append("mechanism=synthetic_sql_load")
    return " ".join(parts)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _is_synthetic_sql(payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return "select sleep" in text or "idb-toolkit" in text or "idb_rnd" in text
