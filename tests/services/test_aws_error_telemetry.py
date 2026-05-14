"""Tests for #1462: AWS service-client error telemetry.

``aws_sdk_client.execute_aws_sdk_call`` and the boto3 entry points in
``lambda_client`` previously swallowed exceptions into terse error dicts
with no Sentry capture. ``botocore.exceptions.ClientError`` (vendor
problem — often expected, e.g. ThrottlingException, AccessDenied) was
indistinguishable from genuine bugs (``RuntimeError`` in our code path),
so post-mortems on degraded investigations were blind.

After this change both paths route through
``app.services.aws._telemetry.capture_aws_error`` with AWS-aware tags:

  surface     = service_client
  integration = aws
  component   = app.services.aws_sdk_client | app.services.lambda_client
  service     = ec2 | lambda | cloudwatch_logs | ...
  operation   = describe_instances | invoke_function | ...
  error_code  = <botocore code>     (ClientError only)
  status_code = 4xx / 5xx           (ClientError only)
  event       = aws_call_failed | aws_extract_failed | aws_decode_failed

``ClientError`` / ``NoCredentialsError`` / ``ParamValidationError`` route
at ``warning`` severity (vendor side, transient, or user-config). Any
other ``Exception`` routes at ``error`` severity (our bug).
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from app.services import aws_sdk_client
from app.services.aws._telemetry import capture_aws_error


@pytest.fixture(autouse=True)
def _quiet_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_NO_TELEMETRY", "1")


def _make_client_error(code: str = "AccessDeniedException", status: int = 403) -> ClientError:
    return ClientError(
        error_response={
            "Error": {"Code": code, "Message": "denied"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation_name="DescribeInstances",
    )


# ---------------------------------------------------------------------------
# Helper smoke tests
# ---------------------------------------------------------------------------


class TestCaptureAwsErrorHelper:
    def test_client_error_uses_warning_severity_and_tags_code(self) -> None:
        exc = _make_client_error("ThrottlingException", 429)
        with patch("app.services.aws._telemetry.report_exception") as mock_report:
            capture_aws_error(
                exc,
                component="app.services.aws_sdk_client",
                service="ec2",
                operation="describe_instances",
                logger=logging.getLogger("test"),
            )
        kwargs = mock_report.call_args.kwargs
        assert kwargs["severity"] == "warning"
        tags = kwargs["tags"]
        assert tags == {
            "surface": "service_client",
            "integration": "aws",
            "component": "app.services.aws_sdk_client",
            "service": "ec2",
            "operation": "describe_instances",
            "event": "aws_call_failed",
            "error_code": "ThrottlingException",
            "status_code": "429",
        }

    def test_no_credentials_error_uses_warning_severity(self) -> None:
        exc = NoCredentialsError()
        with patch("app.services.aws._telemetry.report_exception") as mock_report:
            capture_aws_error(
                exc,
                component="app.services.aws_sdk_client",
                service="s3",
                logger=logging.getLogger("test"),
            )
        kwargs = mock_report.call_args.kwargs
        assert kwargs["severity"] == "warning"
        assert "error_code" not in kwargs["tags"]

    def test_param_validation_error_uses_warning_severity(self) -> None:
        exc = ParamValidationError(report="missing required parameter")
        with patch("app.services.aws._telemetry.report_exception") as mock_report:
            capture_aws_error(
                exc,
                component="app.services.aws_sdk_client",
                service="ec2",
                logger=logging.getLogger("test"),
            )
        assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_generic_exception_uses_error_severity(self) -> None:
        exc = RuntimeError("our bug")
        with patch("app.services.aws._telemetry.report_exception") as mock_report:
            capture_aws_error(
                exc,
                component="app.services.aws_sdk_client",
                service="ec2",
                operation="describe_instances",
                logger=logging.getLogger("test"),
            )
        kwargs = mock_report.call_args.kwargs
        assert kwargs["severity"] == "error"
        tags = kwargs["tags"]
        assert tags["service"] == "ec2"
        # Generic exceptions have no AWS error code metadata.
        assert "error_code" not in tags
        assert "status_code" not in tags

    def test_extra_tags_merge(self) -> None:
        exc = RuntimeError("boom")
        with patch("app.services.aws._telemetry.report_exception") as mock_report:
            capture_aws_error(
                exc,
                component="app.services.lambda_client",
                service="lambda",
                operation="invoke_function",
                logger=logging.getLogger("test"),
                extra_tags={"function_name": "my-fn"},
            )
        assert mock_report.call_args.kwargs["tags"]["function_name"] == "my-fn"


# ---------------------------------------------------------------------------
# aws_sdk_client.execute_aws_sdk_call — all 4 failure branches
# ---------------------------------------------------------------------------


def _patch_boto_to_raise(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    """Stub ``boto3.client(...)`` so its operation raises ``exc``."""

    def fake_operation(**_kwargs: Any) -> None:
        raise exc

    fake_client = MagicMock()
    fake_client.describe_instances = fake_operation
    fake_client.meta = MagicMock(region_name="us-east-1")

    def fake_boto3_client(*_args: Any, **_kwargs: Any) -> MagicMock:
        return fake_client

    monkeypatch.setattr(aws_sdk_client, "boto3", MagicMock(client=fake_boto3_client))


@pytest.mark.parametrize(
    ("exc_factory", "expected_severity", "expected_error_code"),
    [
        (
            lambda: _make_client_error("AccessDeniedException", 403),
            "warning",
            "AccessDeniedException",
        ),
        (NoCredentialsError, "warning", None),
        (lambda: ParamValidationError(report="bad"), "warning", None),
        (lambda: RuntimeError("our bug"), "error", None),
    ],
    ids=["ClientError", "NoCredentialsError", "ParamValidationError", "RuntimeError"],
)
def test_execute_aws_sdk_call_routes_every_branch_to_sentry(
    exc_factory: Any,
    expected_severity: str,
    expected_error_code: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance criterion: every failure path in ``execute_aws_sdk_call``
    must reach Sentry with the right severity + tags. Previously the
    ``except Exception`` and ``ClientError`` branches were silent."""
    exc = exc_factory()
    _patch_boto_to_raise(monkeypatch, exc)

    with patch("app.services.aws_sdk_client.capture_aws_error") as mock_capture:
        result = aws_sdk_client.execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_instances",
        )

    # Caller contract preserved: still returns an error dict, never raises.
    assert result["success"] is False
    assert result["service"] == "ec2"
    assert result["operation"] == "describe_instances"

    mock_capture.assert_called_once()
    kwargs = mock_capture.call_args.kwargs
    assert kwargs["component"] == "app.services.aws_sdk_client"
    assert kwargs["service"] == "ec2"
    assert kwargs["operation"] == "describe_instances"

    # Confirm severity downstream via a direct call to the real helper.
    with patch("app.services.aws._telemetry.report_exception") as mock_report:
        capture_aws_error(
            exc,
            component="app.services.aws_sdk_client",
            service="ec2",
            operation="describe_instances",
            logger=logging.getLogger("test"),
        )
    assert mock_report.call_args.kwargs["severity"] == expected_severity
    if expected_error_code is not None:
        assert mock_report.call_args.kwargs["tags"]["error_code"] == expected_error_code


# ---------------------------------------------------------------------------
# lambda_client — ClientError + generic Exception + JSON decode
# ---------------------------------------------------------------------------


def test_lambda_invoke_function_client_error_reports_and_preserves_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import lambda_client

    fake_client = MagicMock()
    fake_client.invoke.side_effect = _make_client_error("ResourceNotFoundException", 404)
    monkeypatch.setattr(lambda_client, "_get_lambda_client", lambda: fake_client)
    monkeypatch.setattr(lambda_client, "require_aws_credentials", lambda **_k: None)

    with patch("app.services.lambda_client.capture_aws_error") as mock_capture:
        result = lambda_client.invoke_function("my-fn")

    assert result["success"] is False
    mock_capture.assert_called_once()
    kwargs = mock_capture.call_args.kwargs
    assert kwargs["service"] == "lambda"
    assert kwargs["operation"] == "invoke_function"
    assert kwargs["extra_tags"] == {"function_name": "my-fn"}


def test_lambda_invoke_function_generic_exception_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import lambda_client

    fake_client = MagicMock()
    fake_client.invoke.side_effect = RuntimeError("internal")
    monkeypatch.setattr(lambda_client, "_get_lambda_client", lambda: fake_client)
    monkeypatch.setattr(lambda_client, "require_aws_credentials", lambda **_k: None)

    with patch("app.services.lambda_client.capture_aws_error") as mock_capture:
        result = lambda_client.invoke_function("my-fn")

    assert result["success"] is False
    assert "Unexpected error" in result["error"]
    mock_capture.assert_called_once()


def test_lambda_invoke_function_payload_decode_failure_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid JSON in the response Payload was previously uncaught and
    crashed the tool surface. Now it's surfaced with a dedicated event tag."""
    from app.services import lambda_client

    fake_payload = MagicMock()
    fake_payload.read.return_value = b"not-valid-json{"
    fake_client = MagicMock()
    fake_client.invoke.return_value = {
        "StatusCode": 200,
        "Payload": fake_payload,
    }
    monkeypatch.setattr(lambda_client, "_get_lambda_client", lambda: fake_client)
    monkeypatch.setattr(lambda_client, "require_aws_credentials", lambda **_k: None)

    with patch("app.services.lambda_client.capture_aws_error") as mock_capture:
        result = lambda_client.invoke_function("my-fn")

    # The outer call still succeeds — the decode failure is captured as a
    # warning sidecar so callers don't lose the rest of the response.
    assert result["success"] is True
    assert result["data"]["payload"] is None
    assert result["data"]["payload_decode_error"] is not None

    mock_capture.assert_called_once()
    assert mock_capture.call_args.kwargs["event"] == "aws_decode_failed"
