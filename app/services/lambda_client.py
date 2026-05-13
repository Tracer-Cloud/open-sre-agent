"""Lambda client for function inspection and log retrieval."""

import base64
import json
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import requests

from app.services.aws._telemetry import report_aws_service_exception
from app.services.env import make_boto3_client, require_aws_credentials

try:
    from botocore.exceptions import ClientError
except ImportError:

    class ClientError(Exception):  # type: ignore[no-redef]
        """Stub when botocore is not installed; prevents over-broad except clauses."""


def _get_lambda_client():
    return make_boto3_client("lambda")


def _get_cloudwatch_logs_client():
    return make_boto3_client("logs")


_reported_metric_parse_failures: set[str] = set()


def _client_region(client: Any) -> str | None:
    return str(getattr(getattr(client, "meta", None), "region_name", "") or "") or None


def _report_lambda_client_error(
    exc: BaseException,
    *,
    operation: str,
    function_name: str,
    client: Any | None = None,
    severity: str | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    report_aws_service_exception(
        exc,
        service="lambda",
        operation=operation,
        region=_client_region(client),
        function_name=function_name,
        severity=severity,
        extras=extras,
    )


def _report_metric_parse_failure_once(
    exc: BaseException,
    *,
    function_name: str,
    metric_name: str,
    message: str,
    client: Any,
) -> None:
    key = f"{function_name}:{metric_name}"
    if key in _reported_metric_parse_failures:
        return
    _reported_metric_parse_failures.add(key)
    _report_lambda_client_error(
        exc,
        operation="parse_invocation_metric",
        function_name=function_name,
        client=client,
        severity="warning",
        extras={"metric_name": metric_name, "message": message},
    )


def get_function_configuration(function_name: str) -> dict[str, Any]:
    """
    Get Lambda function configuration.

    Args:
        function_name: Lambda function name or ARN

    Returns:
        dict with function configuration
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="lambda_client.get_function_configuration")
    if credentials_error:
        return credentials_error

    try:
        response = client.get_function_configuration(FunctionName=function_name)

        return {
            "success": True,
            "data": {
                "function_name": response.get("FunctionName"),
                "function_arn": response.get("FunctionArn"),
                "runtime": response.get("Runtime"),
                "handler": response.get("Handler"),
                "code_size": response.get("CodeSize"),
                "timeout": response.get("Timeout"),
                "memory_size": response.get("MemorySize"),
                "last_modified": response.get("LastModified"),
                "role": response.get("Role"),
                "environment": response.get("Environment", {}).get("Variables", {}),
                "description": response.get("Description"),
                "version": response.get("Version"),
                "state": response.get("State"),
                "state_reason": response.get("StateReason"),
                "layers": [
                    {"arn": layer.get("Arn"), "code_size": layer.get("CodeSize")}
                    for layer in response.get("Layers", [])
                ],
            },
        }
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="get_function_configuration",
            function_name=function_name,
            client=client,
        )
        return {"success": False, "error": str(e)}


def get_function_code(
    function_name: str,
    extract_files: bool = True,
    max_file_size: int = 10000,
) -> dict[str, Any]:
    """
    Get Lambda function deployment package.

    Args:
        function_name: Lambda function name or ARN
        extract_files: If True, extract and return file contents
        max_file_size: Maximum size in bytes for extracted files

    Returns:
        dict with function code location and optionally file contents
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="lambda_client.get_function_code")
    if credentials_error:
        return credentials_error

    try:
        response = client.get_function(FunctionName=function_name)

        code_location = response.get("Code", {}).get("Location")
        code_size = response.get("Configuration", {}).get("CodeSize", 0)
        repository_type = response.get("Code", {}).get("RepositoryType")

        result: dict[str, Any] = {
            "success": True,
            "data": {
                "function_name": function_name,
                "code_location": code_location,
                "code_size": code_size,
                "repository_type": repository_type,
            },
        }

        if extract_files and code_location and code_size < 5 * 1024 * 1024:
            # Download and extract if code is under 5MB
            try:
                zip_response = requests.get(code_location, timeout=30)
            except requests.RequestException as e:
                _report_lambda_client_error(
                    e,
                    operation="download_function_code",
                    function_name=function_name,
                    client=client,
                    severity="warning",
                    extras={"code_location": code_location},
                )
                zip_response = None
            if zip_response is not None and zip_response.status_code != 200:
                _report_lambda_client_error(
                    RuntimeError(f"Function code download returned HTTP {zip_response.status_code}"),
                    operation="download_function_code",
                    function_name=function_name,
                    client=client,
                    severity="warning",
                    extras={"status_code": zip_response.status_code, "code_location": code_location},
                )
            if zip_response is not None and zip_response.status_code == 200:
                files: dict[str, Any] = {}
                try:
                    with ZipFile(BytesIO(zip_response.content)) as zf:
                        for name in zf.namelist():
                            if name.endswith("/"):
                                continue
                            info = zf.getinfo(name)
                            if info.file_size <= max_file_size:
                                try:
                                    content = zf.read(name).decode("utf-8")
                                    files[name] = {
                                        "size": info.file_size,
                                        "content": content,
                                    }
                                except UnicodeDecodeError:
                                    files[name] = {
                                        "size": info.file_size,
                                        "binary": True,
                                    }
                            else:
                                files[name] = {
                                    "size": info.file_size,
                                    "truncated": True,
                                }
                    result["data"]["files"] = files
                    result["data"]["file_count"] = len(files)
                except Exception as e:
                    _report_lambda_client_error(
                        e,
                        operation="extract_function_code",
                        function_name=function_name,
                        client=client,
                        severity="warning",
                    )
                    result["data"]["extract_error"] = str(e)

        return result
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="get_function",
            function_name=function_name,
            client=client,
        )
        return {"success": False, "error": str(e)}


def get_recent_invocations(
    function_name: str,
    limit: int = 50,
    filter_pattern: str | None = None,
) -> dict[str, Any]:
    """
    Get recent Lambda invocation logs from CloudWatch.

    Lambda logs are stored in /aws/lambda/{function_name}.

    Args:
        function_name: Lambda function name
        limit: Maximum log events to return
        filter_pattern: Optional CloudWatch filter pattern

    Returns:
        dict with invocation logs
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="lambda_client.get_recent_invocations")
    if credentials_error:
        return credentials_error

    log_group_name = f"/aws/lambda/{function_name}"

    try:
        kwargs = {
            "logGroupName": log_group_name,
            "limit": limit,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern

        response = logs_client.filter_log_events(**kwargs)
        events = response.get("events", [])

        # Parse events to identify invocations
        invocations = []
        current_invocation = None

        for event in events:
            message = event.get("message", "")
            timestamp = event.get("timestamp")

            if "START RequestId:" in message:
                if current_invocation:
                    invocations.append(current_invocation)
                request_id = (
                    message.split("RequestId: ")[1].split()[0] if "RequestId:" in message else None
                )
                current_invocation = {
                    "request_id": request_id,
                    "start_time": timestamp,
                    "logs": [message],
                }
            elif "END RequestId:" in message:
                if current_invocation:
                    current_invocation["end_time"] = timestamp
                    current_invocation["logs"].append(message)
            elif "REPORT RequestId:" in message:
                if current_invocation:
                    current_invocation["logs"].append(message)
                    # Parse REPORT for duration and memory
                    if "Duration:" in message:
                        try:
                            duration_part = message.split("Duration: ")[1].split()[0]
                            current_invocation["duration_ms"] = float(duration_part)
                        except (IndexError, ValueError) as e:
                            _report_metric_parse_failure_once(
                                e,
                                function_name=function_name,
                                metric_name="duration_ms",
                                message=message,
                                client=logs_client,
                            )
                    if "Memory Used:" in message:
                        try:
                            memory_part = message.split("Memory Used: ")[1].split()[0]
                            current_invocation["memory_used_mb"] = int(memory_part)
                        except (IndexError, ValueError) as e:
                            _report_metric_parse_failure_once(
                                e,
                                function_name=function_name,
                                metric_name="memory_used_mb",
                                message=message,
                                client=logs_client,
                            )
                    invocations.append(current_invocation)
                    current_invocation = None
            elif current_invocation:
                current_invocation["logs"].append(message)

        if current_invocation:
            invocations.append(current_invocation)

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "invocation_count": len(invocations),
                "invocations": invocations[-10:],  # Return last 10 invocations
                "raw_event_count": len(events),
            },
        }
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="filter_log_events",
            function_name=function_name,
            client=logs_client,
        )
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            return {
                "success": False,
                "error": f"Log group not found: {log_group_name}",
                "log_group": log_group_name,
            }
        return {"success": False, "error": str(e)}


def get_invocation_logs_by_request_id(
    function_name: str,
    request_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get logs for a specific Lambda invocation by request ID.

    Args:
        function_name: Lambda function name
        request_id: Lambda request ID
        limit: Maximum log events to return

    Returns:
        dict with invocation logs
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(
        context="lambda_client.get_invocation_logs_by_request_id"
    )
    if credentials_error:
        return credentials_error

    log_group_name = f"/aws/lambda/{function_name}"

    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            filterPattern=f'"{request_id}"',
            limit=limit,
        )

        events = response.get("events", [])
        log_messages = [event.get("message", "") for event in events]

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "request_id": request_id,
                "event_count": len(events),
                "logs": log_messages,
            },
        }
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="filter_log_events_by_request_id",
            function_name=function_name,
            client=logs_client,
        )
        return {"success": False, "error": str(e)}


def invoke_function(
    function_name: str,
    payload: dict[str, Any] | None = None,
    invocation_type: str = "RequestResponse",
) -> dict[str, Any]:
    """
    Invoke a Lambda function.

    Args:
        function_name: Lambda function name or ARN
        payload: Optional payload dict
        invocation_type: InvocationType (RequestResponse, Event, DryRun)

    Returns:
        dict with invocation result
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="lambda_client.invoke_function")
    if credentials_error:
        return credentials_error

    try:
        kwargs = {
            "FunctionName": function_name,
            "InvocationType": invocation_type,
        }
        if payload:
            kwargs["Payload"] = json.dumps(payload)

        response = client.invoke(**kwargs)

        result_payload = None
        payload_parse_error = None
        if "Payload" in response:
            raw_payload = response["Payload"].read().decode()
            try:
                result_payload = json.loads(raw_payload)
            except json.JSONDecodeError as e:
                _report_lambda_client_error(
                    e,
                    operation="parse_invoke_payload",
                    function_name=function_name,
                    client=client,
                    extras={"payload_preview": raw_payload[:200]},
                )
                payload_parse_error = str(e)

        data = {
            "status_code": response.get("StatusCode"),
            "function_error": response.get("FunctionError"),
            "executed_version": response.get("ExecutedVersion"),
            "log_result": base64.b64decode(response.get("LogResult", "")).decode()
            if response.get("LogResult")
            else None,
            "payload": result_payload,
        }
        if payload_parse_error is not None:
            data["payload_parse_error"] = payload_parse_error

        return {
            "success": True,
            "data": data,
        }
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="invoke",
            function_name=function_name,
            client=client,
        )
        return {"success": False, "error": str(e)}


def list_functions(
    max_items: int = 50,
    function_version: str = "ALL",
) -> dict[str, Any]:
    """
    List Lambda functions in the account.

    Args:
        max_items: Maximum functions to return
        function_version: ALL or specific version

    Returns:
        dict with function list
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}
    credentials_error = require_aws_credentials(context="lambda_client.list_functions")
    if credentials_error:
        return credentials_error

    try:
        response = client.list_functions(
            FunctionVersion=function_version,
            MaxItems=max_items,
        )

        functions = []
        for func in response.get("Functions", []):
            functions.append(
                {
                    "function_name": func.get("FunctionName"),
                    "function_arn": func.get("FunctionArn"),
                    "runtime": func.get("Runtime"),
                    "handler": func.get("Handler"),
                    "code_size": func.get("CodeSize"),
                    "timeout": func.get("Timeout"),
                    "memory_size": func.get("MemorySize"),
                    "last_modified": func.get("LastModified"),
                }
            )

        return {
            "success": True,
            "data": {
                "functions": functions,
                "count": len(functions),
            },
        }
    except ClientError as e:
        _report_lambda_client_error(
            e,
            operation="list_functions",
            function_name="",
            client=client,
        )
        return {"success": False, "error": str(e)}
