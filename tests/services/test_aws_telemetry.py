from __future__ import annotations

from unittest.mock import ANY, patch

from botocore.exceptions import ClientError

from app.services.aws._telemetry import report_aws_service_exception


def test_report_aws_service_exception_tags_client_error_as_warning() -> None:
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetFunction",
    )

    with patch("app.services.aws._telemetry.report_exception") as report:
        report_aws_service_exception(
            error,
            service="lambda",
            operation="get_function",
            region="us-east-1",
            function_name="fn",
        )

    report.assert_called_once()
    _, kwargs = report.call_args
    assert kwargs["severity"] == "warning"
    assert kwargs["tags"]["error_code"] == "AccessDeniedException"
    assert kwargs["tags"]["region"] == "us-east-1"
    assert kwargs["tags"]["function_name"] == "fn"


def test_report_aws_service_exception_tags_unknown_error_as_error() -> None:
    error = RuntimeError("boom")

    with patch("app.services.aws._telemetry.report_exception") as report:
        report_aws_service_exception(
            error,
            service="ec2",
            operation="describe_instances",
        )

    report.assert_called_once_with(
        error,
        logger=ANY,
        message="[aws] ec2.describe_instances failed: RuntimeError",
        severity="error",
        tags={
            "surface": "service_client",
            "cloud": "aws",
            "service": "ec2",
            "operation": "describe_instances",
            "error_code": "RuntimeError",
        },
        extras=None,
    )
