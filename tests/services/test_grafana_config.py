from __future__ import annotations

import pytest

from app.services.grafana.config import GrafanaAccountConfig


def test_grafana_config_normalizes_instance_url() -> None:
    """Test that trailing slashes and whitespace are stripped from the instance URL."""
    config = GrafanaAccountConfig(
        account_id="test-acc",
        instance_url=" https://grafana.example.com/  ",
        read_token="secret",
    )
    assert config.instance_url == "https://grafana.example.com"


@pytest.mark.parametrize(
    "url, token, expected",
    [
        ("http://localhost:3000", "", True),
        ("http://127.0.0.1:3000", "", True),
        ("http://0.0.0.0:3000", "", True),
        ("http://localhost:3000", "has-token", False),  # Token presence disables anon auth
        ("https://grafana.example.com", "", False),  # External host requires token
        ("", "", False),  # Empty URL
    ],
)
def test_grafana_config_uses_local_anonymous_auth(url: str, token: str, expected: bool) -> None:
    """Test the logic for determining if local anonymous auth is allowed."""
    config = GrafanaAccountConfig(
        account_id="test",
        instance_url=url,
        read_token=token,
    )
    assert config.uses_local_anonymous_auth is expected


@pytest.mark.parametrize(
    "url, token, expected",
    [
        ("https://grafana.com", "token", True),  # Standard config
        ("http://localhost:3000", "", True),  # Local anon config
        ("https://grafana.com", "", False),  # Hosted without token
        ("", "token", False),  # Missing URL
        ("", "", False),  # Fully unconfigured
    ],
)
def test_grafana_config_is_configured(url: str, token: str, expected: bool) -> None:
    """Test that is_configured correctly identifies valid setups."""
    config = GrafanaAccountConfig(
        account_id="test",
        instance_url=url,
        read_token=token,
    )
    assert config.is_configured is expected
