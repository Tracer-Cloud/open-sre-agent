"""Unit tests for the WhatsApp notification provider."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from app.notifications.models import NotificationEvent
from app.notifications.providers.whatsapp import WhatsAppProvider


@pytest.fixture
def whatsapp_provider():
    return WhatsAppProvider()


@pytest.fixture
def sample_event():
    return NotificationEvent(
        title="Test Incident",
        body="This is a test investigation report.",
        severity="critical",
        investigation_url="https://opensre.ai/i/123"
    )


def test_whatsapp_probe_missing_url(whatsapp_provider):
    """Should fail if webhook URL is missing."""
    success, detail = whatsapp_provider.probe({})
    assert not success
    assert "Missing WhatsApp" in detail


@patch("app.notifications.providers.whatsapp.post_json")
def test_whatsapp_send_notification_success(mock_post, whatsapp_provider, sample_event):
    """Should successfully send a formatted notification."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.status_code = 200
    mock_post.return_value = mock_response

    config = {"webhook_url": "https://hooks.whatsapp.test/123"}
    success, error = whatsapp_provider.send_notification(sample_event, config)

    assert success
    assert not error
    
    # Verify the payload contains the formatted text
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    payload = kwargs["payload"]
    assert "[CRITICAL] Test Incident" in payload["text"]
    assert "View details: https://opensre.ai/i/123" in payload["text"]


@patch("app.notifications.providers.whatsapp.post_json")
def test_whatsapp_send_notification_failure(mock_post, whatsapp_provider, sample_event):
    """Should return error detail on HTTP failure."""
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.status_code = 500
    mock_response.error = "Internal Server Error"
    mock_post.return_value = mock_response

    config = {"webhook_url": "https://hooks.whatsapp.test/123"}
    success, error = whatsapp_provider.send_notification(sample_event, config)

    assert not success
    assert "Internal Server Error" in error
