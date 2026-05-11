"""AWS evidence section builders for root cause diagnosis prompts."""

from __future__ import annotations

import json
from typing import Any

from app.nodes.root_cause_diagnosis.formatters import _db_load_value, _format_wait_events


def build_cloudwatch_section(evidence: dict[str, Any], cloudwatch_url: str | None = None) -> str:
    cloudwatch_logs = evidence.get("cloudwatch_logs", [])[:5]
    if not cloudwatch_logs:
        return ""

    lines = [f"\nCloudWatch Error Logs ({len(cloudwatch_logs)} events):\n"]
    for log in cloudwatch_logs:
        lines.append(f"{log}\n")
    if cloudwatch_url:
        lines.append(f"\n[Citation: View full logs at {cloudwatch_url}]\n")
    lines.append("\n")
    return "".join(lines)


def build_batch_section(evidence: dict[str, Any]) -> str:
    failed_jobs = evidence.get("failed_jobs", [])
    if not failed_jobs:
        return ""

    lines = [f"\nAWS Batch Failed Jobs ({len(failed_jobs)}):\n"]
    for job in failed_jobs[:5]:
        lines.append(
            f"- {job.get('job_name', 'Unknown')}: {job.get('status_reason', 'No reason')}\n"
        )
    return "".join(lines)


def build_failed_tools_section(evidence: dict[str, Any]) -> str:
    failed_tools = evidence.get("failed_tools", [])
    if not failed_tools:
        return ""

    lines = [f"\nFailed Tools ({len(failed_tools)}):\n"]
    for tool in failed_tools[:5]:
        lines.append(f"- {tool.get('tool_name', 'Unknown')}: exit_code={tool.get('exit_code')}\n")
    return "".join(lines)


def build_error_logs_section(evidence: dict[str, Any]) -> str:
    error_logs = evidence.get("error_logs", [])[:10]
    if not error_logs:
        return ""

    lines = [f"\nError Logs ({len(error_logs)}):\n"]
    for log in error_logs[:5]:
        lines.append(f"- {log.get('message', '')[:200]}\n")
    return "".join(lines)


def build_host_metrics_section(evidence: dict[str, Any]) -> str:
    host_metrics = evidence.get("host_metrics", {})
    if not (host_metrics and host_metrics.get("data")):
        return ""
    return "\nHost Metrics: Available (CPU, memory, disk)\n"


def build_rds_metrics_section(evidence: dict[str, Any]) -> str:
    """Build RDS CloudWatch metrics evidence section."""
    rds_metrics = evidence.get("aws_cloudwatch_metrics", {})
    if not rds_metrics:
        return ""

    lines = ["\nRDS CloudWatch Metrics:\n"]

    db_instance = rds_metrics.get("db_instance_identifier")
    if db_instance:
        lines.append(f"- DB instance: {db_instance}\n")

    for metric in rds_metrics.get("metrics", [])[:8]:
        if not isinstance(metric, dict):
            continue
        metric_name = metric.get("metric_name", "unknown")
        summary = metric.get("summary")
        unit = metric.get("unit")
        if summary:
            lines.append(f"- {metric_name}: {summary}\n")
        else:
            datapoints = metric.get("recent_datapoints", [])
            entry = f"- {metric_name}: {len(datapoints)} recent datapoints"
            if unit:
                entry += f" [{unit}]"
            lines.append(entry + "\n")

    for observation in rds_metrics.get("observations", [])[:5]:
        lines.append(f"- Observation: {observation}\n")

    return "".join(lines)


def build_rds_events_section(evidence: dict[str, Any]) -> str:
    """Build RDS event evidence section."""
    rds_events = evidence.get("aws_rds_events", [])
    if not rds_events:
        return ""

    lines = [f"\nRDS Events ({len(rds_events)}):\n"]
    for event in rds_events[:8]:
        if not isinstance(event, dict):
            continue
        timestamp = event.get("date") or event.get("timestamp") or "unknown-time"
        message = event.get("message", "No message")
        source = event.get("source_identifier") or event.get("source_type")
        entry = f"- {timestamp}: {message}"
        if source:
            entry += f" [{source}]"
        lines.append(entry + "\n")
    return "".join(lines)


def build_performance_insights_section(evidence: dict[str, Any]) -> str:
    """Build RDS Performance Insights evidence section."""
    performance_insights = evidence.get("aws_performance_insights", {})
    if not performance_insights:
        return ""

    lines = ["\nPerformance Insights:\n"]

    if performance_insights.get("db_instance_identifier"):
        lines.append(f"- DB instance: {performance_insights['db_instance_identifier']}\n")

    for observation in performance_insights.get("observations", [])[:5]:
        lines.append(f"- Observation: {observation}\n")

    top_sql = performance_insights.get("top_sql", [])
    if top_sql:
        lines.append("Top SQL:\n")
        for item in top_sql[:5]:
            if not isinstance(item, dict):
                continue
            sql = str(item.get("sql") or item.get("statement") or "")[:180]
            db_load = item.get("db_load", item.get("db_load_avg"))
            wait_event = item.get("wait_event")
            if not wait_event and isinstance(item.get("wait_events"), list):
                wait_event = ", ".join(
                    str(wait.get("name", "unknown"))
                    for wait in item.get("wait_events", [])[:3]
                    if isinstance(wait, dict)
                )
            entry = f"- sql={sql}"
            if db_load is not None:
                entry += f" | db_load={db_load}"
            if wait_event:
                entry += f" | wait_event={wait_event}"
            wait_events = item.get("wait_events", [])
            if isinstance(wait_events, list) and wait_events:
                entry += f" | wait_events={_format_wait_events(wait_events)}"
            if item.get("calls_per_sec") is not None:
                entry += f" | calls_per_sec={item['calls_per_sec']}"
            lines.append(entry + "\n")

    wait_events = (
        performance_insights.get("wait_events") or performance_insights.get("top_wait_events") or []
    )
    if wait_events:
        lines.append("Top Wait Events:\n")
        for item in wait_events[:5]:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "unknown")
            db_load = _db_load_value(item)
            entry = f"- {name}"
            if item.get("type"):
                entry += f" [{item['type']}]"
            if db_load is not None:
                entry += f" | db_load={db_load}"
            lines.append(entry + "\n")

    top_users = performance_insights.get("top_users", [])
    if top_users:
        lines.append("Top Users:\n")
        for item in top_users[:5]:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "unknown")
            db_load = _db_load_value(item)
            entry = f"- {name}"
            if db_load is not None:
                entry += f" | db_load={db_load}"
            lines.append(entry + "\n")

    top_hosts = performance_insights.get("top_hosts", [])
    if top_hosts:
        lines.append("Top Hosts:\n")
        for item in top_hosts[:5]:
            if not isinstance(item, dict):
                continue
            host = item.get("id") or item.get("host") or "unknown"
            db_load = _db_load_value(item)
            entry = f"- {host}"
            if db_load is not None:
                entry += f" | db_load={db_load}"
            lines.append(entry + "\n")

    return "".join(lines)


def build_lambda_logs_section(evidence: dict[str, Any]) -> str:
    lambda_logs = evidence.get("lambda_logs", [])[:10]
    if not lambda_logs:
        return ""

    lines = [f"\nLambda Invocation Logs ({len(lambda_logs)} events):\n"]
    for log in lambda_logs[:10]:
        message = log.get("message", "") if isinstance(log, dict) else str(log)
        lines.append(f"- {message[:300]}\n")
    return "".join(lines)


def build_lambda_function_section(evidence: dict[str, Any]) -> str:
    """Build Lambda function evidence section."""
    lambda_function = evidence.get("lambda_function", {})
    if not (lambda_function and lambda_function.get("function_name")):
        return ""

    lines = ["\nLambda Function Configuration:\n"]
    lines.append(f"- Function: {lambda_function.get('function_name')}\n")
    lines.append(f"- Runtime: {lambda_function.get('runtime')}\n")
    lines.append(f"- Handler: {lambda_function.get('handler')}\n")

    if lambda_function.get("environment_variables"):
        env_vars = lambda_function.get("environment_variables", {})
        lines.append(f"- Environment Variables: {', '.join(env_vars.keys())}\n")

    code_files = lambda_function.get("code", {}).get("files")
    if isinstance(code_files, dict) and code_files:
        lines.append(f"- Code Files: {', '.join(list(code_files.keys())[:5])}\n")
        handler_file = lambda_function.get("handler", "").split(".")[0] + ".py"
        if handler_file in code_files:
            file_content = code_files.get(handler_file, "")
            if isinstance(file_content, str):
                lines.append(f"\nHandler Code Snippet ({handler_file}):\n{file_content[:1000]}\n")

    return "".join(lines)


def build_lambda_config_section(evidence: dict[str, Any]) -> str:
    """Build Lambda configuration evidence section."""
    lambda_config = evidence.get("lambda_config", {})
    if not (lambda_config and lambda_config.get("function_name")):
        return ""

    lines = ["\nLambda Configuration:\n"]
    lines.append(f"- Function: {lambda_config.get('function_name')}\n")
    lines.append(f"- Runtime: {lambda_config.get('runtime')}\n")
    lines.append(f"- Handler: {lambda_config.get('handler')}\n")

    env_vars = lambda_config.get("environment_variables", {})
    if env_vars:
        lines.append("- Environment Variables:\n")
        for key, value in list(env_vars.items())[:10]:
            lines.append(f"  - {key}: {value}\n")

    return "".join(lines)


def build_s3_object_section(evidence: dict[str, Any]) -> str:
    """Build S3 object evidence section."""
    s3_object = evidence.get("s3_object", {})
    if not (s3_object and s3_object.get("found")):
        return ""

    lines = ["\nS3 Object Details:\n"]
    lines.append(f"- Bucket: {s3_object.get('bucket')}\n")
    lines.append(f"- Key: {s3_object.get('key')}\n")
    lines.append(f"- Size: {s3_object.get('size')} bytes\n")
    lines.append(f"- Content Type: {s3_object.get('content_type')}\n")

    metadata = s3_object.get("metadata", {})
    if metadata:
        lines.append(f"- Metadata: {json.dumps(metadata, indent=2)}\n")

    sample = s3_object.get("sample")
    if s3_object.get("is_text") and isinstance(sample, str):
        lines.append(f"\nS3 Object Sample:\n{sample[:500]}\n")

    return "".join(lines)


def build_s3_audit_section(evidence: dict[str, Any]) -> str:
    """Build S3 audit payload evidence section."""
    s3_audit_payload = evidence.get("s3_audit_payload", {})
    if not (s3_audit_payload and s3_audit_payload.get("found")):
        return ""

    lines = ["\nS3 Audit Payload (External API Lineage):\n"]
    lines.append(f"- Bucket: {s3_audit_payload.get('bucket')}\n")
    lines.append(f"- Key: {s3_audit_payload.get('key')}\n")

    audit_content = s3_audit_payload.get("content")
    if audit_content:
        try:
            audit_data = (
                json.loads(audit_content) if isinstance(audit_content, str) else audit_content
            )
            lines.append(f"- Content: {json.dumps(audit_data, indent=2)[:1500]}\n")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"- Content: {str(audit_content)[:500]}\n")

    return "".join(lines)


def build_vendor_audit_section(evidence: dict[str, Any]) -> str:
    """Build vendor audit evidence section."""
    vendor_audit_from_logs = evidence.get("vendor_audit_from_logs", {})
    if not (vendor_audit_from_logs and vendor_audit_from_logs.get("requests")):
        return ""

    lines = ["\nExternal Vendor API Audit (from Lambda logs):\n"]
    for req in vendor_audit_from_logs.get("requests", [])[:5]:
        lines.append(f"- {req.get('type')} {req.get('url')}\n")
        lines.append(f"  Status: {req.get('status_code')}\n")
        if req.get("response_body"):
            response_str = json.dumps(req.get("response_body"), indent=2)[:500]
            lines.append(f"  Response: {response_str}\n")

    return "".join(lines)
