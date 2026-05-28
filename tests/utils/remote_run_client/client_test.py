"""Tests for remote_run_client."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests.utils.conftest import REMOTE_RUN_REMOTE_STREAM_URL
from tests.utils.remote_run_client import fire_alert_to_remote_run_stream


class TestRemoteRunClient(unittest.TestCase):
    @patch("tests.utils.remote_run_client.client.requests.post")
    def test_fire_alert_to_remote_run_stream_success(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        raw_alert = {"test": "data"}
        response = fire_alert_to_remote_run_stream(
            alert_name="Test Alert",
            pipeline_name="test-pipeline",
            severity="critical",
            raw_alert=raw_alert,
        )

        self.assertEqual(response, mock_response)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], REMOTE_RUN_REMOTE_STREAM_URL)
        self.assertEqual(kwargs["json"]["input"]["alert_name"], "Test Alert")
        self.assertEqual(kwargs["json"]["input"]["pipeline_name"], "test-pipeline")
        self.assertEqual(kwargs["json"]["input"]["severity"], "critical")
        self.assertEqual(kwargs["json"]["input"]["raw_alert"], raw_alert)

    @patch("tests.utils.remote_run_client.client.requests.post")
    def test_fire_alert_to_remote_run_stream_failure(self, mock_post: MagicMock) -> None:
        import requests

        mock_post.side_effect = requests.exceptions.HTTPError("500 Server Error")

        with self.assertRaises(requests.exceptions.HTTPError):
            fire_alert_to_remote_run_stream(
                alert_name="Test Alert",
                pipeline_name="test-pipeline",
                severity="critical",
                raw_alert={"test": "data"},
            )
