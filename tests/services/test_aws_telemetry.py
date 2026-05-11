"""Tests for ``app.services.aws_telemetry`` and the AWS-client error paths
that route through it. Regression coverage for issue #1462."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests as requests_module
from botocore.exceptions import ClientError

from app.services import aws_sdk_client, aws_telemetry, lambda_client


def _client_error(code: str = "AccessDeniedException", message: str = "denied") -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        operation_name="DescribeInstances",
    )


class TestAwsErrorCode:
    def test_extracts_code_from_client_error(self) -> None:
        assert (
            aws_telemetry.aws_error_code(_client_error("ThrottlingException"))
            == "ThrottlingException"
        )

    def test_returns_unknown_for_non_client_error(self) -> None:
        assert aws_telemetry.aws_error_code(RuntimeError("boom")) == "Unknown"

    def test_returns_unknown_when_response_payload_missing_code(self) -> None:
        exc = ClientError(
            error_response={"Error": {}, "ResponseMetadata": {}},
            operation_name="DescribeInstances",
        )
        assert aws_telemetry.aws_error_code(exc) == "Unknown"


class TestReportAwsFailure:
    def test_client_error_is_reported_as_warning_with_aws_tags(self) -> None:
        logger = logging.getLogger("test.aws_telemetry")
        exc = _client_error("AccessDeniedException")

        with patch.object(aws_telemetry, "report_exception") as mock_report:
            aws_telemetry.report_aws_failure(
                exc,
                logger=logger,
                message="boom",
                service="ec2",
                operation="describe_instances",
                region="us-east-1",
            )

        assert mock_report.call_count == 1
        kwargs = mock_report.call_args.kwargs
        assert kwargs["severity"] == "warning"
        assert kwargs["tags"] == {
            "surface": "service_client",
            "component": "aws",
            "service": "ec2",
            "operation": "describe_instances",
            "region": "us-east-1",
            "aws_error_code": "AccessDeniedException",
        }

    def test_unexpected_exception_is_reported_as_error_without_aws_code_tag(self) -> None:
        logger = logging.getLogger("test.aws_telemetry")

        with patch.object(aws_telemetry, "report_exception") as mock_report:
            aws_telemetry.report_aws_failure(
                RuntimeError("boom"),
                logger=logger,
                message="unexpected",
                service="ec2",
                operation="describe_instances",
            )

        assert mock_report.call_count == 1
        kwargs = mock_report.call_args.kwargs
        assert kwargs["severity"] == "error"
        assert "aws_error_code" not in kwargs["tags"]
        assert kwargs["tags"]["surface"] == "service_client"
        assert kwargs["tags"]["component"] == "aws"


class TestExecuteAwsSdkCallReportsErrors:
    """``execute_aws_sdk_call`` previously swallowed ``ClientError`` and
    ``Exception`` without logging or capturing — see #1462."""

    def test_client_error_path_calls_report_aws_failure(self) -> None:
        with (
            patch.object(aws_sdk_client, "boto3") as mock_boto3,
            patch.object(aws_sdk_client, "report_aws_failure") as mock_report,
        ):
            client = MagicMock()
            mock_boto3.client.return_value = client
            client.describe_instances.side_effect = _client_error("AccessDeniedException")

            result = aws_sdk_client.execute_aws_sdk_call("ec2", "describe_instances")

        assert result["success"] is False
        assert result["metadata"]["error_code"] == "AccessDeniedException"
        assert mock_report.call_count == 1
        assert isinstance(mock_report.call_args.args[0], ClientError)

    def test_unexpected_exception_path_calls_report_aws_failure(self) -> None:
        with (
            patch.object(aws_sdk_client, "boto3") as mock_boto3,
            patch.object(aws_sdk_client, "report_aws_failure") as mock_report,
        ):
            client = MagicMock()
            mock_boto3.client.return_value = client
            client.describe_instances.side_effect = RuntimeError("boom")

            result = aws_sdk_client.execute_aws_sdk_call("ec2", "describe_instances")

        assert result["success"] is False
        assert result["metadata"]["error_type"] == "unexpected"
        assert mock_report.call_count == 1
        assert isinstance(mock_report.call_args.args[0], RuntimeError)


@pytest.mark.parametrize(
    "factory_name, side_effect, expect_extract_error_substring",
    [
        ("requests_request_exception", "download failed", "download failed"),
        ("zip_extraction_exception", "BadZipFile", "BadZipFile"),
    ],
)
def test_get_function_routes_extract_failures_through_helper(
    factory_name: str,
    side_effect: str,
    expect_extract_error_substring: str,
) -> None:
    """``get_function_code`` previously surfaced extract failures only
    inside the result dict — see #1462."""
    function_name = "fn-1"
    code_url = "https://example.invalid/code.zip"

    with (
        patch.object(lambda_client, "_get_lambda_client") as mock_get_client,
        patch.object(lambda_client, "require_aws_credentials", return_value=None),
        patch.object(lambda_client, "report_aws_failure") as mock_report,
        patch("requests.get") as mock_requests_get,
    ):
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_function.return_value = {
            "Code": {"Location": code_url, "RepositoryType": "S3"},
            "Configuration": {"CodeSize": 1024},
        }
        if factory_name == "requests_request_exception":
            mock_requests_get.side_effect = requests_module.RequestException(side_effect)
        else:
            zip_response = MagicMock()
            zip_response.status_code = 200
            zip_response.content = b"not a zip"
            mock_requests_get.return_value = zip_response

        result = lambda_client.get_function_code(function_name, extract_files=True)

    assert mock_report.call_count == 1
    assert "extract_error" in result["data"]


def test_invoke_function_captures_json_decode_error() -> None:
    """``invoke_function`` previously let ``JSONDecodeError`` propagate
    uncontrolled — see #1462."""
    with (
        patch.object(lambda_client, "_get_lambda_client") as mock_get_client,
        patch.object(lambda_client, "require_aws_credentials", return_value=None),
        patch.object(lambda_client, "report_aws_failure") as mock_report,
    ):
        client = MagicMock()
        mock_get_client.return_value = client
        payload_stream = MagicMock()
        payload_stream.read.return_value = b"not json"
        client.invoke.return_value = {"Payload": payload_stream, "StatusCode": 200}

        result = lambda_client.invoke_function("fn-1")

    assert result["success"] is False
    assert "Invalid JSON payload" in result["error"]
    assert mock_report.call_count == 1
