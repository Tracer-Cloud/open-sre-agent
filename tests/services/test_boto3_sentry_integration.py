"""Integration tests for boto3 Sentry capture in AWS SDK clients."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from app.services.aws_sdk_client import execute_aws_sdk_call
from app.services.lambda_client import get_function_configuration


class TestAWSSDKClientSentryIntegration:
    """Test Sentry capture in aws_sdk_client.py."""

    @patch("app.services.aws_sdk_client.capture_boto3_exception")
    @patch("app.services.aws_sdk_client.boto3.client")
    def test_captures_no_credentials_error(
        self, mock_boto_client: MagicMock, mock_capture: MagicMock
    ) -> None:
        """NoCredentialsError should be captured with Sentry."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.describe_instances.side_effect = NoCredentialsError()

        result = execute_aws_sdk_call("ec2", "describe_instances", {})

        assert result["success"] is False
        assert "credentials" in result["error"].lower()
        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert isinstance(args[0], NoCredentialsError)
        assert kwargs["service"] == "ec2"
        assert kwargs["operation"] == "describe_instances"

    @patch("app.services.aws_sdk_client.capture_boto3_exception")
    @patch("app.services.aws_sdk_client.boto3.client")
    def test_captures_param_validation_error(
        self, mock_boto_client: MagicMock, mock_capture: MagicMock
    ) -> None:
        """ParamValidationError should be captured with Sentry."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.describe_instances.side_effect = ParamValidationError(
            report="Invalid parameter"
        )

        result = execute_aws_sdk_call("ec2", "describe_instances", {})

        assert result["success"] is False
        assert "Invalid parameters" in result["error"]
        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert isinstance(args[0], ParamValidationError)

    @patch("app.services.aws_sdk_client.capture_boto3_exception")
    @patch("app.services.aws_sdk_client.boto3.client")
    def test_captures_client_error(
        self, mock_boto_client: MagicMock, mock_capture: MagicMock
    ) -> None:
        """ClientError should be captured with Sentry."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        error_response = {
            "Error": {"Code": "AccessDenied", "Message": "Access denied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }
        mock_client.describe_instances.side_effect = ClientError(
            error_response, "DescribeInstances"
        )

        result = execute_aws_sdk_call("ec2", "describe_instances", {})

        assert result["success"] is False
        assert "AccessDenied" in result["error"]
        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert isinstance(args[0], ClientError)
        assert kwargs["service"] == "ec2"
        assert kwargs["operation"] == "describe_instances"

    @patch("app.services.aws_sdk_client.capture_boto3_exception")
    @patch("app.services.aws_sdk_client.boto3.client")
    def test_captures_generic_exception(
        self, mock_boto_client: MagicMock, mock_capture: MagicMock
    ) -> None:
        """Generic Exception should be captured with Sentry."""
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client
        mock_client.describe_instances.side_effect = RuntimeError("Unexpected error")

        result = execute_aws_sdk_call("ec2", "describe_instances", {})

        assert result["success"] is False
        assert "Unexpected error" in result["error"]
        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert isinstance(args[0], RuntimeError)


class TestLambdaClientSentryIntegration:
    """Test Sentry capture in lambda_client.py."""

    @patch("app.services.lambda_client.capture_boto3_exception")
    @patch("app.services.lambda_client.make_boto3_client")
    @patch("app.services.lambda_client.require_aws_credentials")
    def test_captures_client_error_in_get_function_configuration(
        self,
        mock_require_creds: MagicMock,
        mock_make_client: MagicMock,
        mock_capture: MagicMock,
    ) -> None:
        """ClientError in get_function_configuration should be captured."""
        mock_require_creds.return_value = None
        mock_client = MagicMock()
        mock_make_client.return_value = mock_client
        error_response = {
            "Error": {"Code": "ResourceNotFoundException", "Message": "Not found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        mock_client.get_function_configuration.side_effect = ClientError(
            error_response, "GetFunctionConfiguration"
        )

        result = get_function_configuration("test-function")

        assert result["success"] is False
        mock_capture.assert_called_once()
        args, kwargs = mock_capture.call_args
        assert isinstance(args[0], ClientError)
        assert kwargs["service"] == "lambda"
        assert kwargs["operation"] == "get_function_configuration"
        assert kwargs["extras"]["function_name"] == "test-function"

# Made with Bob
